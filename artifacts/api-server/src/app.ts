import express, { type Express } from "express";
import cors from "cors";
import pinoHttp from "pino-http";
import path from "path";
import { existsSync } from "fs";
import router from "./routes";
import { logger } from "./lib/logger";
import { requireApiKey } from "./middlewares/auth";

const app: Express = express();

app.use(
  pinoHttp({
    logger,
    serializers: {
      req(req) {
        return {
          id: req.id,
          method: req.method,
          url: req.url?.split("?")[0],
        };
      },
      res(res) {
        return {
          statusCode: res.statusCode,
        };
      },
    },
  }),
);
app.use(cors());
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

app.use("/api", (req, res, next) => {
  const mutating = ["POST", "PUT", "PATCH", "DELETE"].includes(req.method);
  if (mutating) return requireApiKey(req, res, next);
  return next();
});
app.use("/api", router);

// Serve the dashboard SPA in production (static files built by dashboard artifact)
const dashboardDist = path.resolve(
  path.dirname(new URL(import.meta.url).pathname),
  "../../dashboard/dist/public",
);
if (existsSync(dashboardDist)) {
  app.use(express.static(dashboardDist));
  app.use((_req, res) => {
    res.sendFile(path.join(dashboardDist, "index.html"));
  });
}

export default app;
