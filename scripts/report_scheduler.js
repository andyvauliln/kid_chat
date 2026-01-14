const cron = require("node-cron");
const { spawn } = require("child_process");

const PYTHON_BIN = process.env.PYTHON_BIN || "python";
const REPORT_SEND = process.env.REPORT_SEND !== "false";
const TZ = process.env.REPORT_TZ || "UTC";

const argv = new Set(process.argv.slice(2));
const isDryRun = argv.has("--dry-run");
const runOnce = argv.has("--run-once");

const formatDate = (d) => d.toISOString().slice(0, 10);
const addDays = (d, days) => {
  const out = new Date(d);
  out.setDate(out.getDate() + days);
  return out;
};

const runModule = (moduleName, args) =>
  new Promise((resolve, reject) => {
    const fullArgs = ["-m", moduleName, ...args];
    const proc = spawn(PYTHON_BIN, fullArgs, { stdio: "inherit" });
    proc.on("error", reject);
    proc.on("exit", (code) => {
      if (code === 0) {
        resolve();
      } else {
        reject(new Error(`${moduleName} exited with code ${code}`));
      }
    });
  });

const buildArgs = ({ date, noSend }) => {
  const out = ["--date", date];
  if (noSend) {
    out.push("--no-send");
  }
  return out;
};

const tasks = [
  {
    name: "morning_plan",
    cron: "0 7 * * *",
    module: "group_chat_telegram_ai.morning_plan",
    date: () => formatDate(new Date()),
  },
  {
    name: "daily_report",
    cron: "30 22 * * *",
    module: "group_chat_telegram_ai.daily_report",
    date: () => formatDate(new Date()),
  },
  {
    name: "weekly_report",
    cron: "30 0 * * 1",
    module: "group_chat_telegram_ai.weekly_report",
    date: () => formatDate(addDays(new Date(), -1)),
  },
  {
    name: "monthly_report",
    cron: "45 0 1 * *",
    module: "group_chat_telegram_ai.monthly_report",
    date: () => formatDate(addDays(new Date(), -1)),
  },
];

const runTask = async (task, { noSend }) => {
  const dateValue = task.date();
  const args = buildArgs({ date: dateValue, noSend });
  console.log(`[scheduler] ${task.name} -> ${PYTHON_BIN} -m ${task.module} ${args.join(" ")}`);
  await runModule(task.module, args);
};

const main = async () => {
  if (isDryRun) {
    console.log("[scheduler] dry-run mode");
    tasks.forEach((task) => {
      console.log(`[scheduler] ${task.name} schedule="${task.cron}" tz="${TZ}"`);
    });
    return;
  }

  if (runOnce) {
    for (const task of tasks) {
      await runTask(task, { noSend: !REPORT_SEND });
    }
    return;
  }

  tasks.forEach((task) => {
    cron.schedule(
      task.cron,
      () => runTask(task, { noSend: !REPORT_SEND }).catch((err) => console.error(err)),
      { timezone: TZ }
    );
  });

  console.log(`[scheduler] started with timezone=${TZ} send=${REPORT_SEND ? "true" : "false"}`);
};

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
