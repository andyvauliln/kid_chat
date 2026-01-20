"""
Agent command for Telegram bot.

Runs Claude Code CLI with full project context to execute code changes
based on user requests from Telegram.

Features:
- Plan-first flow: Shows plan before execution, waits for confirmation
- Session management: Keeps conversation context between messages
- Self-improvement: Can edit its own code when asked
- Logging: Records all agent actions to agent_logs.jsonl
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, MessageHandler, filters

from .handle_message import send_telegram_long_text


REPO_ROOT = Path(__file__).parent.parent.parent
FILES_CONTEXT_PATH = REPO_ROOT / "data" / "app_pages" / "Files Context.md"
AGENT_LOGS_PATH = REPO_ROOT / "data" / "agent_logs.jsonl"
AGENT_SESSIONS_PATH = REPO_ROOT / "data" / "agent_sessions.json"
TEST_CASES_PATH = REPO_ROOT / "tests" / "agent_test_cases.json"
TEST_RESULTS_PATH = REPO_ROOT / "data" / "agent_test_results.json"

SESSION_TIMEOUT_MINUTES = 30

SessionState = Literal[
    "planning",
    "awaiting_confirmation",
    "awaiting_answer",
    "executing",
    "done",
    "cancelled",
]


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value or not value.strip():
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@dataclass
class AgentResult:
    """Result from running the Claude agent."""
    request: str
    response: str
    plan: str = ""
    question: str = ""
    has_question: bool = False
    duration_seconds: float = 0.0
    status: str = "success"
    files_read: list[str] = field(default_factory=list)
    files_edited: list[str] = field(default_factory=list)
    files_created: list[str] = field(default_factory=list)
    raw_output: str = ""
    cost_usd: float | None = None
    claude_session_id: str = ""
    error: str | None = None


def _load_files_context() -> str:
    """Load the Files Context.md content."""
    if not FILES_CONTEXT_PATH.exists():
        return "(Files Context not found)"
    return FILES_CONTEXT_PATH.read_text(encoding="utf-8")


def _build_system_prompt() -> str:
    """Build the system prompt with project context."""
    files_context = _load_files_context()
    return f"""You are a coding agent for this project.

PROJECT CONTEXT:
{files_context}

WORKING DIRECTORY: {REPO_ROOT}

RULES:
1. You can read, edit, and create files in this project
2. Before making changes, explain your plan clearly
3. Ask clarifying questions if the request is ambiguous
4. After changes, provide a summary of what was modified
5. You CAN edit your own source code in src/group_chat_telegram_ai/agent_command.py when asked to improve yourself
6. Update data/app_pages/Files Context.md if you create new files or significantly change structure

RESPONSE FORMAT:
When planning, structure your response as:
PLAN:
1. Step one
2. Step two
...
FILES TO MODIFY:
- file1.py
- file2.py

When asking questions:
QUESTION:
Your question here?

When done executing:
DONE:
Summary of changes made."""


# ============================================================================
# Session Management
# ============================================================================

def _load_sessions() -> dict:
    """Load sessions from file."""
    if not AGENT_SESSIONS_PATH.exists():
        return {"sessions": {}}
    try:
        return json.loads(AGENT_SESSIONS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"sessions": {}}


def _save_sessions(data: dict) -> None:
    """Save sessions to file."""
    AGENT_SESSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    AGENT_SESSIONS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _session_key(update: Update) -> str:
    """Generate a unique session key for chat+user."""
    chat_id = update.message.chat_id if update.message else 0
    user_id = update.message.from_user.id if update.message and update.message.from_user else 0
    return f"chat_{chat_id}_user_{user_id}"


def _get_active_session(key: str) -> dict | None:
    """Get active session if not expired."""
    sessions = _load_sessions()
    session = sessions.get("sessions", {}).get(key)
    if not session:
        return None
    
    # Check timeout
    last_activity = datetime.fromisoformat(session.get("last_activity", "2000-01-01T00:00:00+00:00"))
    if datetime.now(timezone.utc) - last_activity > timedelta(minutes=SESSION_TIMEOUT_MINUTES):
        return None  # Expired
    
    # Don't return completed or cancelled sessions
    if session.get("state") in ("done", "cancelled"):
        return None
    
    return session


def _save_session(key: str, session: dict) -> None:
    """Save a session."""
    sessions = _load_sessions()
    sessions["sessions"][key] = session
    _save_sessions(sessions)


def _end_session(key: str) -> None:
    """End/remove a session."""
    sessions = _load_sessions()
    if key in sessions.get("sessions", {}):
        sessions["sessions"][key]["state"] = "cancelled"
        sessions["sessions"][key]["last_activity"] = datetime.now(timezone.utc).isoformat()
    _save_sessions(sessions)


# ============================================================================
# Logging
# ============================================================================

def _append_agent_log(result: AgentResult, username: str, session_id: str) -> None:
    """Append agent run to log file."""
    try:
        AGENT_LOGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "datetime": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "username": username,
            "request": result.request,
            "duration_seconds": result.duration_seconds,
            "status": result.status,
            "actions": {
                "files_read": result.files_read,
                "files_edited": result.files_edited,
                "files_created": result.files_created,
            },
            "response_summary": result.response[:500] if result.response else "",
            "cost_usd": result.cost_usd,
            "error": result.error,
        }
        with AGENT_LOGS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # Best effort logging


def _format_summary(result: AgentResult, session_id: str) -> str:
    """Format a summary for Telegram."""
    lines = [
        f"SESSION #{session_id}",
        f"Status: {result.status}",
        f"Duration: {result.duration_seconds:.1f}s",
        "",
    ]
    
    if result.files_read or result.files_edited or result.files_created:
        lines.append("Actions:")
        if result.files_read:
            lines.append(f"  Read: {', '.join(result.files_read[:5])}")
            if len(result.files_read) > 5:
                lines.append(f"    ... and {len(result.files_read) - 5} more")
        if result.files_edited:
            lines.append(f"  Edited: {', '.join(result.files_edited)}")
        if result.files_created:
            lines.append(f"  Created: {', '.join(result.files_created)}")
        lines.append("")
    
    if result.response:
        lines.append(result.response[:1500])
    
    return "\n".join(lines)


# ============================================================================
# Claude CLI Execution
# ============================================================================

def _parse_claude_output(raw_output: str) -> AgentResult:
    """Parse Claude CLI output to extract actions and response."""
    result = AgentResult(request="", response="", raw_output=raw_output)
    
    # Extract files read/edited/created from output
    # Look for patterns like "Read file: path" or "Edited: path"
    read_pattern = re.compile(r"(?:Read|Reading|read)\s+(?:file:?\s*)?([^\n]+\.(?:py|md|json|js|ts|txt))", re.IGNORECASE)
    edit_pattern = re.compile(r"(?:Edit|Edited|Editing|Modified|Updated)\s+(?:file:?\s*)?([^\n]+\.(?:py|md|json|js|ts|txt))", re.IGNORECASE)
    create_pattern = re.compile(r"(?:Create|Created|Creating|Write|Wrote)\s+(?:file:?\s*)?([^\n]+\.(?:py|md|json|js|ts|txt))", re.IGNORECASE)
    
    result.files_read = list(set(read_pattern.findall(raw_output)))
    result.files_edited = list(set(edit_pattern.findall(raw_output)))
    result.files_created = list(set(create_pattern.findall(raw_output)))
    
    # Check if response contains a question
    if "QUESTION:" in raw_output or raw_output.strip().endswith("?"):
        result.has_question = True
        # Extract the question
        if "QUESTION:" in raw_output:
            q_start = raw_output.find("QUESTION:")
            q_text = raw_output[q_start + 9:].strip()
            # Find end of question (next section or end)
            for marker in ["PLAN:", "DONE:", "\n\n\n"]:
                if marker in q_text:
                    q_text = q_text[:q_text.find(marker)].strip()
                    break
            result.question = q_text
    
    # Extract plan if present
    if "PLAN:" in raw_output:
        p_start = raw_output.find("PLAN:")
        p_text = raw_output[p_start + 5:].strip()
        for marker in ["QUESTION:", "DONE:", "\n\n\n"]:
            if marker in p_text:
                p_text = p_text[:p_text.find(marker)].strip()
                break
        result.plan = p_text
    
    # Set response to the full output for now
    result.response = raw_output.strip()
    
    return result


async def run_claude_agent(
    message: str,
    mode: Literal["plan", "execute"] = "plan",
    resume_session: str | None = None,
    session_id: str | None = None,
) -> AgentResult:
    """
    Run Claude CLI with the given message.
    
    Args:
        message: User's request or reply
        mode: "plan" for planning only, "execute" for full execution
        resume_session: Claude session ID to resume
        session_id: Our session ID for logging
    
    Returns:
        AgentResult with response and metadata
    """
    start_time = datetime.now(timezone.utc)
    system_prompt = _build_system_prompt()
    
    # Build command
    cmd = ["claude", "-p", "--print"]
    
    # Add system prompt
    cmd.extend(["--append-system-prompt", system_prompt])
    
    # Set permission mode based on our mode
    if mode == "plan":
        cmd.extend(["--permission-mode", "plan"])
    else:
        cmd.extend(["--permission-mode", "default"])
    
    # Resume session if provided
    if resume_session:
        cmd.extend(["--resume", resume_session])
    
    # Output format
    cmd.extend(["--output-format", "text"])
    
    # Add the message
    cmd.append(message)
    
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(REPO_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "CLAUDE_CODE_ENTRYPOINT": "telegram-agent"},
        )
        
        # Set timeout (5 minutes)
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=300.0
            )
        except asyncio.TimeoutError:
            process.kill()
            return AgentResult(
                request=message,
                response="Agent timed out after 5 minutes.",
                status="error",
                error="timeout",
                duration_seconds=(datetime.now(timezone.utc) - start_time).total_seconds(),
            )
        
        raw_output = stdout.decode("utf-8", errors="replace")
        stderr_output = stderr.decode("utf-8", errors="replace")
        
        if process.returncode != 0 and not raw_output.strip():
            return AgentResult(
                request=message,
                response=f"Agent error: {stderr_output or 'Unknown error'}",
                status="error",
                error=stderr_output,
                duration_seconds=(datetime.now(timezone.utc) - start_time).total_seconds(),
            )
        
        # Parse the output
        result = _parse_claude_output(raw_output)
        result.request = message
        result.duration_seconds = (datetime.now(timezone.utc) - start_time).total_seconds()
        result.status = "success"
        result.claude_session_id = session_id or ""
        
        return result
        
    except FileNotFoundError:
        return AgentResult(
            request=message,
            response="Claude CLI not found. Make sure 'claude' is installed and in PATH.",
            status="error",
            error="claude_not_found",
            duration_seconds=(datetime.now(timezone.utc) - start_time).total_seconds(),
        )
    except Exception as e:
        return AgentResult(
            request=message,
            response=f"Agent error: {str(e)}",
            status="error",
            error=str(e),
            duration_seconds=(datetime.now(timezone.utc) - start_time).total_seconds(),
        )


# ============================================================================
# Telegram Command Handlers
# ============================================================================

async def _send_text(update: Update, text: str) -> None:
    """Send text to Telegram, handling long messages."""
    if not update.message:
        return
    bot_token = _require_env("TELEGRAM_BOT_TOKEN")
    await send_telegram_long_text(bot_token=bot_token, chat_id=update.message.chat_id, text=text)


def _strip_command(text: str, command: str) -> str:
    """Strip command prefix from text."""
    if not text:
        return ""
    pattern = rf"^/{re.escape(command)}(?:@\w+)?\s*"
    return re.sub(pattern, "", text.strip(), count=1).strip()


def _get_username(update: Update) -> str:
    """Get username from update."""
    if update.message and update.message.from_user:
        return update.message.from_user.username or update.message.from_user.first_name or "unknown"
    return "unknown"


async def agent_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /agent command.
    
    Usage:
        /agent <request>     - Start new session or continue with new request
        /agent new <request> - Force new session
        /agent cancel        - Cancel current session
        /agent status        - Show session status
    """
    if not update.message:
        return
    
    text = (update.message.text or "").strip()
    message = _strip_command(text, "agent")
    key = _session_key(update)
    username = _get_username(update)
    
    # Handle special commands
    if message.lower() == "cancel":
        _end_session(key)
        await _send_text(update, "Session cancelled.")
        return
    
    if message.lower() == "status":
        session = _get_active_session(key)
        if session:
            state = session.get("state", "unknown")
            sid = session.get("session_id", "?")
            await _send_text(update, f"SESSION #{sid}\nState: {state}\nRequest: {session.get('request', '?')[:100]}")
        else:
            await _send_text(update, "No active session.")
        return
    
    # Force new session
    if message.lower().startswith("new "):
        message = message[4:].strip()
        _end_session(key)
    
    if not message:
        await _send_text(update, "Usage: /agent <your request>\n\nExamples:\n- /agent add error handling to bot.py\n- /agent explain how daily_report.py works\n- /agent improve yourself by adding better logging")
        return
    
    # Start new session
    session_id = str(uuid.uuid4())[:8]
    
    await _send_text(update, f"SESSION #{session_id} | Planning...")
    
    # Run Claude in plan mode
    result = await run_claude_agent(
        message=message,
        mode="plan",
        session_id=session_id,
    )
    
    # Log the run
    _append_agent_log(result, username, session_id)
    
    if result.status == "error":
        await _send_text(update, f"Error: {result.response}")
        return
    
    # Save session
    _save_session(key, {
        "session_id": session_id,
        "claude_session_id": result.claude_session_id,
        "state": "awaiting_confirmation" if not result.has_question else "awaiting_answer",
        "request": message,
        "plan": result.plan or result.response,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_activity": datetime.now(timezone.utc).isoformat(),
        "messages": [
            {"role": "user", "content": message, "at": datetime.now(timezone.utc).isoformat()},
            {"role": "agent", "content": result.response, "at": datetime.now(timezone.utc).isoformat()},
        ],
    })
    
    # Send response
    if result.has_question:
        await _send_text(update, f"SESSION #{session_id}\n\nQUESTION\n{result.question or result.response}\n\n(Reply with your answer)")
    else:
        await _send_text(update, f"SESSION #{session_id}\n\nPLAN\n{result.plan or result.response}\n\nReply: 'ok' to execute, or give feedback to revise")


async def agent_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle replies to agent messages (continuation of session).
    
    This handles:
    - "ok" / "yes" -> Execute the plan
    - Any other text -> Feedback to revise plan or answer to question
    """
    if not update.message:
        return
    
    text = (update.message.text or "").strip()
    if not text:
        return
    
    # Don't handle commands
    if text.startswith("/"):
        return
    
    key = _session_key(update)
    session = _get_active_session(key)
    
    if not session:
        # No active session - ignore non-command messages
        return
    
    username = _get_username(update)
    session_id = session.get("session_id", "?")
    state = session.get("state", "")
    
    # Update session activity
    session["last_activity"] = datetime.now(timezone.utc).isoformat()
    session["messages"].append({
        "role": "user",
        "content": text,
        "at": datetime.now(timezone.utc).isoformat(),
    })
    
    if state == "awaiting_confirmation":
        if text.lower() in ("ok", "yes", "do it", "proceed", "go", "execute", "run"):
            # Execute the plan
            session["state"] = "executing"
            _save_session(key, session)
            
            await _send_text(update, f"SESSION #{session_id} | Executing...")
            
            result = await run_claude_agent(
                message="Please proceed with the plan you outlined. Execute the changes.",
                mode="execute",
                resume_session=session.get("claude_session_id"),
                session_id=session_id,
            )
            
            # Log execution
            _append_agent_log(result, username, session_id)
            
            session["state"] = "done"
            session["messages"].append({
                "role": "agent",
                "content": result.response,
                "at": datetime.now(timezone.utc).isoformat(),
            })
            _save_session(key, session)
            
            # Send summary
            summary = _format_summary(result, session_id)
            await _send_text(update, f"DONE\n\n{summary}")
        else:
            # User gave feedback - revise plan
            await _send_text(update, f"SESSION #{session_id} | Revising plan...")
            
            result = await run_claude_agent(
                message=f"User feedback: {text}\n\nPlease revise your plan based on this feedback.",
                mode="plan",
                resume_session=session.get("claude_session_id"),
                session_id=session_id,
            )
            
            _append_agent_log(result, username, session_id)
            
            if result.has_question:
                session["state"] = "awaiting_answer"
                session["messages"].append({
                    "role": "agent",
                    "content": result.response,
                    "at": datetime.now(timezone.utc).isoformat(),
                })
                _save_session(key, session)
                await _send_text(update, f"SESSION #{session_id}\n\nQUESTION\n{result.question or result.response}")
            else:
                session["state"] = "awaiting_confirmation"
                session["plan"] = result.plan or result.response
                session["messages"].append({
                    "role": "agent",
                    "content": result.response,
                    "at": datetime.now(timezone.utc).isoformat(),
                })
                _save_session(key, session)
                await _send_text(update, f"SESSION #{session_id}\n\nREVISED PLAN\n{result.plan or result.response}\n\nReply: 'ok' to execute, or give feedback")
    
    elif state == "awaiting_answer":
        # User is answering a question
        await _send_text(update, f"SESSION #{session_id} | Processing answer...")
        
        result = await run_claude_agent(
            message=text,
            mode="plan",
            resume_session=session.get("claude_session_id"),
            session_id=session_id,
        )
        
        _append_agent_log(result, username, session_id)
        
        if result.has_question:
            session["state"] = "awaiting_answer"
            session["messages"].append({
                "role": "agent",
                "content": result.response,
                "at": datetime.now(timezone.utc).isoformat(),
            })
            _save_session(key, session)
            await _send_text(update, f"SESSION #{session_id}\n\nQUESTION\n{result.question or result.response}")
        else:
            session["state"] = "awaiting_confirmation"
            session["plan"] = result.plan or result.response
            session["messages"].append({
                "role": "agent",
                "content": result.response,
                "at": datetime.now(timezone.utc).isoformat(),
            })
            _save_session(key, session)
            await _send_text(update, f"SESSION #{session_id}\n\nPLAN\n{result.plan or result.response}\n\nReply: 'ok' to execute, or give feedback")


# ============================================================================
# Test Commands
# ============================================================================

def _load_test_cases() -> list[dict]:
    """Load test cases from file."""
    if not TEST_CASES_PATH.exists():
        return []
    try:
        data = json.loads(TEST_CASES_PATH.read_text(encoding="utf-8"))
        return data.get("test_cases", [])
    except Exception:
        return []


def _load_test_results() -> dict:
    """Load test results from file."""
    if not TEST_RESULTS_PATH.exists():
        return {"last_run": None, "results": {}}
    try:
        return json.loads(TEST_RESULTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"last_run": None, "results": {}}


def _save_test_results(results: dict) -> None:
    """Save test results to file."""
    TEST_RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    results["last_run"] = datetime.now(timezone.utc).isoformat()
    TEST_RESULTS_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")


def _find_next_test(test_cases: list[dict], results: dict) -> dict | None:
    """Find first test that is not passed and not skipped."""
    for tc in test_cases:
        test_id = str(tc.get("id", ""))
        result = results.get("results", {}).get(test_id)
        if result is None or result.get("status") not in ("passed", "skipped"):
            return tc
    return None


def _format_test_output(test_case: dict, agent_result: AgentResult) -> str:
    """Format test output for display."""
    lines = [
        f"TEST #{test_case.get('id')} [{test_case.get('category', '?')}]",
        f"Request: {test_case.get('input', '?')}",
        "",
        "Expected:",
        f"  Actions: {', '.join(test_case.get('expected_actions', []))}",
        f"  Files: {', '.join(test_case.get('expected_files', []))}",
        f"  Response contains: {', '.join(test_case.get('expected_response_contains', []))}",
        "",
        "--- AGENT OUTPUT ---",
        agent_result.response[:2000] if agent_result.response else "(no response)",
        "",
        "--- CHANGES DETECTED ---",
    ]
    
    if agent_result.files_read:
        lines.append(f"Read: {', '.join(agent_result.files_read[:10])}")
    if agent_result.files_edited:
        lines.append(f"Edited: {', '.join(agent_result.files_edited)}")
    if agent_result.files_created:
        lines.append(f"Created: {', '.join(agent_result.files_created)}")
    if not (agent_result.files_read or agent_result.files_edited or agent_result.files_created):
        lines.append("(no file changes)")
    
    lines.append("")
    lines.append("Reply: ok / not / skip")
    
    return "\n".join(lines)


async def agent_test_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Run interactive agent tests.
    
    Usage:
        /agent_test        - Start/continue testing
        /agent_test status - Show test progress
        /agent_test reset  - Reset all test results
    """
    if not update.message:
        return
    
    text = _strip_command(update.message.text or "", "agent_test")
    key = _session_key(update)
    
    # Handle sub-commands
    if text.lower() == "status":
        await _agent_test_status(update)
        return
    
    if text.lower() == "reset":
        _save_test_results({"last_run": None, "results": {}})
        await _send_text(update, "Test results reset.")
        return
    
    # Check if user is responding to a test
    test_context = context.user_data.get("agent_test_context")
    if test_context and text.lower() in ("ok", "not", "skip"):
        current_test_id = test_context.get("current_test_id")
        if current_test_id:
            results = _load_test_results()
            status_map = {"ok": "passed", "not": "failed", "skip": "skipped"}
            results["results"][str(current_test_id)] = {
                "status": status_map[text.lower()],
                "run_at": datetime.now(timezone.utc).isoformat(),
            }
            _save_test_results(results)
            await _send_text(update, f"Test #{current_test_id} marked as {status_map[text.lower()]}.")
    
    # Find and run next test
    test_cases = _load_test_cases()
    if not test_cases:
        await _send_text(update, "No test cases found. Create tests/agent_test_cases.json first.")
        return
    
    results = _load_test_results()
    next_test = _find_next_test(test_cases, results)
    
    if not next_test:
        # All done - show summary
        passed = sum(1 for r in results.get("results", {}).values() if r.get("status") == "passed")
        failed = sum(1 for r in results.get("results", {}).values() if r.get("status") == "failed")
        skipped = sum(1 for r in results.get("results", {}).values() if r.get("status") == "skipped")
        await _send_text(update, f"All tests completed!\n\nPassed: {passed}\nFailed: {failed}\nSkipped: {skipped}\nTotal: {len(test_cases)}")
        return
    
    # Run the test
    test_input = next_test.get("input", "").replace("/agent ", "")
    await _send_text(update, f"Running TEST #{next_test.get('id')}...")
    
    agent_result = await run_claude_agent(
        message=test_input,
        mode="plan",
        session_id=f"test-{next_test.get('id')}",
    )
    
    # Store test context
    context.user_data["agent_test_context"] = {
        "current_test_id": next_test.get("id"),
    }
    
    # Format and send output
    output = _format_test_output(next_test, agent_result)
    await _send_text(update, output)


async def _agent_test_status(update: Update) -> None:
    """Show test status."""
    test_cases = _load_test_cases()
    results = _load_test_results()
    
    if not test_cases:
        await _send_text(update, "No test cases found.")
        return
    
    passed = sum(1 for r in results.get("results", {}).values() if r.get("status") == "passed")
    failed = sum(1 for r in results.get("results", {}).values() if r.get("status") == "failed")
    skipped = sum(1 for r in results.get("results", {}).values() if r.get("status") == "skipped")
    pending = len(test_cases) - passed - failed - skipped
    
    lines = [
        "TEST STATUS",
        f"Passed: {passed}",
        f"Failed: {failed}",
        f"Skipped: {skipped}",
        f"Pending: {pending}",
        f"Total: {len(test_cases)}",
    ]
    
    if results.get("last_run"):
        lines.append(f"\nLast run: {results['last_run']}")
    
    await _send_text(update, "\n".join(lines))


# ============================================================================
# Handler Registration
# ============================================================================

def build_agent_handlers() -> list:
    """Build all agent-related handlers."""
    return [
        CommandHandler("agent", agent_command),
        CommandHandler("agent_test", agent_test_command),
    ]


def build_agent_reply_handler():
    """Build the reply handler for agent sessions."""
    return MessageHandler(
        filters.TEXT & ~filters.COMMAND & (
            filters.ChatType.PRIVATE |
            filters.REPLY
        ),
        agent_reply_handler,
    )
