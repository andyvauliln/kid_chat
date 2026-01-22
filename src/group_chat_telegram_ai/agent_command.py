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
UUID_PATTERN = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

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
    if resume_session and _is_uuid(resume_session):
        cmd.extend(["--resume", resume_session])
    
    # Output format
    cmd.extend(["--output-format", "text"])
    
    # Add the message (use -- to avoid option parsing)
    cmd.append("--")
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
        extracted_session_id = _extract_uuid(f"{raw_output}\n{stderr_output}")
        
        if not raw_output.strip() and stderr_output.strip():
            return AgentResult(
                request=message,
                response=f"Agent error: {stderr_output}",
                status="error",
                error=stderr_output,
                duration_seconds=(datetime.now(timezone.utc) - start_time).total_seconds(),
            )
        
        if not raw_output.strip() and process.returncode == 0:
            return AgentResult(
                request=message,
                response="Agent returned empty response.",
                status="error",
                error="empty_response",
                duration_seconds=(datetime.now(timezone.utc) - start_time).total_seconds(),
            )
        
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
        result.claude_session_id = extracted_session_id
        
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


def _is_uuid(value: str | None) -> bool:
    """Check if value is a UUID string."""
    if not value:
        return False
    return bool(UUID_PATTERN.fullmatch(value.strip()))


def _extract_uuid(text: str) -> str:
    """Extract a UUID from text if present."""
    if not text:
        return ""
    matches = UUID_PATTERN.findall(text)
    return matches[-1] if matches else ""


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

    # Allow agent test replies ("ok"/"not"/"skip") without /agent_test
    test_context = context.user_data.get("agent_test_context")
    if test_context and text.lower() in ("ok", "not", "skip"):
        await agent_test_command(update, context)
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


async def agent_test_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle plain-text replies for agent tests in any chat.
    """
    if not update.message:
        return
    
    text = (update.message.text or "").strip()
    if not text or text.startswith("/"):
        return
    
    if not context.user_data.get("agent_test_context"):
        return
    
    await agent_test_command(update, context)


# ============================================================================
# Test Commands
# ============================================================================

def _load_test_cases_data() -> dict:
    """Load test cases data from file."""
    if not TEST_CASES_PATH.exists():
        return {"test_cases": []}
    try:
        data = json.loads(TEST_CASES_PATH.read_text(encoding="utf-8"))
        if "test_cases" not in data:
            data["test_cases"] = []
        normalized = _normalize_test_cases_data(data)
        if normalized is not None:
            data = normalized
            _save_test_cases_data(data)
        return data
    except Exception:
        return {"test_cases": []}


def _normalize_test_cases_data(data: dict) -> dict | None:
    """Ensure test cases include status/plan/result fields."""
    changed = False
    for test_case in data.get("test_cases", []):
        if "status" not in test_case:
            test_case["status"] = "untested"
            changed = True
        if "plan" not in test_case:
            test_case["plan"] = ""
            changed = True
        if "result" not in test_case:
            test_case["result"] = ""
            changed = True
    return data if changed else None


def _save_test_cases_data(data: dict) -> None:
    """Save test cases data to file."""
    TEST_CASES_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_test_cases() -> list[dict]:
    """Load test cases list from file."""
    return _load_test_cases_data().get("test_cases", [])


def _update_test_case(test_id: int | str, updates: dict) -> dict | None:
    """Update a single test case by id."""
    data = _load_test_cases_data()
    for test_case in data.get("test_cases", []):
        if str(test_case.get("id")) == str(test_id):
            test_case.update(updates)
            _save_test_cases_data(data)
            return test_case
    return None


def _get_test_case(test_id: int | str) -> dict | None:
    """Get a single test case by id."""
    data = _load_test_cases_data()
    for test_case in data.get("test_cases", []):
        if str(test_case.get("id")) == str(test_id):
            return test_case
    return None


def _find_next_test(test_cases: list[dict]) -> dict | None:
    """Find first test that is untested."""
    for tc in test_cases:
        status = (tc.get("status") or "untested").lower()
        if status == "untested":
            return tc
    return None


def _format_test_output(test_case: dict, agent_result: AgentResult) -> str:
    """Format test output for display."""
    lines = [
        f"TEST #{test_case.get('id')} [{test_case.get('category', '?')}]",
        f"Request: {test_case.get('input', '?')}",
        f"Status: {(test_case.get('status') or 'untested')}",
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
    lines.append("Reply: ok / not / skip, or send feedback to revise (or /agent_test ok)")
    
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
        data = _load_test_cases_data()
        for test_case in data.get("test_cases", []):
            test_case["status"] = "untested"
            test_case.pop("plan", None)
            test_case.pop("result", None)
        _save_test_cases_data(data)
        await _send_text(update, "Test results reset.")
        return

    if text.lower() in ("ok", "not", "skip") and not context.user_data.get("agent_test_context"):
        await _send_text(update, "No active test. Run /agent_test to start.")
        return
    
    # Check if user is responding to a test
    test_context = context.user_data.get("agent_test_context")
    if test_context:
        current_test_id = test_context.get("current_test_id")
        if text.lower() in ("ok", "not", "skip"):
            if current_test_id:
                status_map = {"ok": "passed", "not": "skiped", "skip": "skiped"}
                result_map = {"ok": "passed", "not": "failed", "skip": "skipped"}
                updated = _update_test_case(
                    current_test_id,
                    {
                        "status": status_map[text.lower()],
                        "result": result_map[text.lower()],
                    },
                )
                if updated:
                    await _send_text(update, f"Test #{current_test_id} marked as {status_map[text.lower()]}.")
            # Clear test context for next test
            context.user_data.pop("agent_test_context", None)
        elif current_test_id:
            # Treat any other text as feedback to revise the plan
            await _send_text(update, f"TEST #{current_test_id} | Revising plan...")
            agent_result = await run_claude_agent(
                message=f"User feedback: {text}\n\nPlease revise your plan based on this feedback.",
                mode="plan",
                resume_session=test_context.get("claude_session_id"),
                session_id=f"test-{current_test_id}",
            )
            if agent_result.status != "error":
                _update_test_case(
                    current_test_id,
                    {
                        "plan": agent_result.plan or agent_result.response,
                        "result": agent_result.response,
                    },
                )
            context.user_data["agent_test_context"] = {
                "current_test_id": current_test_id,
                "claude_session_id": agent_result.claude_session_id,
            }
            test_case = _get_test_case(current_test_id) or {"id": current_test_id}
            output = _format_test_output(test_case, agent_result)
            await _send_text(update, output)
            return
    
    # Find and run next test
    test_cases = _load_test_cases()
    if not test_cases:
        await _send_text(update, "No test cases found. Create tests/agent_test_cases.json first.")
        return
    
    next_test = _find_next_test(test_cases)
    
    if not next_test:
        # All done - show summary
        passed = sum(1 for tc in test_cases if (tc.get("status") or "untested") == "passed")
        skipped = sum(1 for tc in test_cases if (tc.get("status") or "untested") == "skiped")
        pending = sum(1 for tc in test_cases if (tc.get("status") or "untested") == "untested")
        await _send_text(update, f"All tests completed!\n\nPassed: {passed}\nSkipped: {skipped}\nPending: {pending}\nTotal: {len(test_cases)}")
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
        "claude_session_id": agent_result.claude_session_id,
    }
    if agent_result.status != "error":
        _update_test_case(
            next_test.get("id"),
            {
                "plan": agent_result.plan or agent_result.response,
                "result": agent_result.response,
            },
        )
    
    # Format and send output
    output = _format_test_output(next_test, agent_result)
    await _send_text(update, output)


async def _agent_test_status(update: Update) -> None:
    """Show test status."""
    test_cases = _load_test_cases()
    
    if not test_cases:
        await _send_text(update, "No test cases found.")
        return
    
    passed = sum(1 for tc in test_cases if (tc.get("status") or "untested") == "passed")
    skipped = sum(1 for tc in test_cases if (tc.get("status") or "untested") == "skiped")
    pending = sum(1 for tc in test_cases if (tc.get("status") or "untested") == "untested")
    
    lines = [
        "TEST STATUS",
        f"Passed: {passed}",
        f"Skipped: {skipped}",
        f"Pending: {pending}",
        f"Total: {len(test_cases)}",
    ]
    
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


def build_agent_test_reply_handler():
    """Build the reply handler for agent tests."""
    return MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        agent_test_reply_handler,
    )
