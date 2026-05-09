import { describe, it, expect, vi, afterEach } from "vitest";
import type { Request, Response, NextFunction } from "express";
import { requireApiKey } from "./auth";

function mockReq(headers: Record<string, string> = {}): Request {
  return { headers, method: "POST" } as unknown as Request;
}

function mockRes() {
  const res = {
    status: vi.fn().mockReturnThis(),
    json: vi.fn().mockReturnThis(),
  };
  return res as unknown as Response;
}

afterEach(() => {
  delete process.env.INTERNAL_API_KEY;
});

describe("requireApiKey", () => {
  it("passes through when INTERNAL_API_KEY is not set (dev mode)", () => {
    const next = vi.fn() as unknown as NextFunction;
    requireApiKey(mockReq(), mockRes(), next);
    expect(next).toHaveBeenCalledOnce();
  });

  it("returns 401 when key header is absent", () => {
    process.env.INTERNAL_API_KEY = "secret-key";
    const next = vi.fn() as unknown as NextFunction;
    const res = mockRes();
    requireApiKey(mockReq(), res, next);
    expect((res.status as ReturnType<typeof vi.fn>)).toHaveBeenCalledWith(401);
    expect(next).not.toHaveBeenCalled();
  });

  it("returns 401 when key header is wrong", () => {
    process.env.INTERNAL_API_KEY = "secret-key";
    const next = vi.fn() as unknown as NextFunction;
    const res = mockRes();
    requireApiKey(mockReq({ "x-api-key": "wrong" }), res, next);
    expect((res.status as ReturnType<typeof vi.fn>)).toHaveBeenCalledWith(401);
    expect(next).not.toHaveBeenCalled();
  });

  it("calls next when key matches", () => {
    process.env.INTERNAL_API_KEY = "secret-key";
    const next = vi.fn() as unknown as NextFunction;
    const res = mockRes();
    requireApiKey(mockReq({ "x-api-key": "secret-key" }), res, next);
    expect(next).toHaveBeenCalledOnce();
    expect((res.status as ReturnType<typeof vi.fn>)).not.toHaveBeenCalled();
  });
});
