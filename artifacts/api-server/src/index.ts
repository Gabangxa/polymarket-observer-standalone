import app from "./app";
import { logger } from "./lib/logger";

const port = Number(process.env["PORT"] ?? "8080");

if (Number.isNaN(port) || port <= 0) {
  throw new Error(`Invalid PORT value: "${process.env["PORT"]}"`);
}

if (process.env.NODE_ENV === "production" && !process.env.INTERNAL_API_KEY) {
  logger.error("INTERNAL_API_KEY is not set — refusing to start in production without auth");
  process.exit(1);
}

app.listen(port, "0.0.0.0", (err) => {
  if (err) {
    logger.error({ err }, "Error listening on port");
    process.exit(1);
  }
  logger.info({ port }, "Server listening");
});
