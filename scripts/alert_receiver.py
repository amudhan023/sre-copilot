"""Run the SRE Copilot Alertmanager webhook receiver locally."""

import os

import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        "sre_copilot.alerting.receiver:app",
        host="0.0.0.0",
        port=int(os.getenv("RECEIVER_PORT", "8080")),
    )
