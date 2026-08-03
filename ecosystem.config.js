module.exports = {
  apps: [
    {
      name: "bill-extractor-backend",
      script: "python3",
      args: "-m uvicorn main:app --host 0.0.0.0 --port 8000 --log-level info",
      cwd: __dirname,
      interpreter: "python3",
      exec_mode: "fork",
      instances: 1,
      autorestart: true,
      restart_delay: 5000,
      max_restarts: 10,
      min_uptime: "200s",
      max_memory_restart: "512M",
      kill_timeout: 3000,
      merge_logs: true,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      output: "./logs/bill-extractor-out.log",
      error: "./logs/bill-extractor-error.log",
      env: {
        PYTHONUNBUFFERED: "1",
        LOG_LEVEL: "info",
      },
      env_production: {
        PYTHONUNBUFFERED: "1",
        LOG_LEVEL: "info",
      },
    },
  ],
};
