import type { RequestHandler } from "express";

/**
 * Requires `X-API-Key` header to match `INTERNAL_API_KEY` env var on mutating
 * requests. When the env var is unset (local dev), all requests pass through.
 */
export const requireApiKey: RequestHandler = (req, res, next) => {
  const configured = process.env.INTERNAL_API_KEY?.trim();
  if (!configured) {
    next();
    return;
  }
  const provided = (req.headers["x-api-key"] as string | undefined)?.trim();
  if (provided !== configured) {
    res.status(401).json({ error: "Unauthorized" });
    return;
  }
  next();
};
