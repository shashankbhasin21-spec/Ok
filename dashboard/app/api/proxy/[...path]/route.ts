import { NextRequest, NextResponse } from "next/server";

const API = process.env.FIRM_API_URL || "http://127.0.0.1:8787";

async function forward(req: NextRequest, path: string[]) {
  const suffix = path.join("/");
  const url = `${API}/api/${suffix}${req.nextUrl.search}`;
  const headers: Record<string, string> = {};
  const ct = req.headers.get("content-type");
  if (ct) headers["Content-Type"] = ct;
  const owner = req.headers.get("x-owner-secret");
  if (owner) headers["X-Owner-Secret"] = owner;
  const session = req.headers.get("x-payout-session");
  if (session) headers["X-Payout-Session"] = session;

  const init: RequestInit = { method: req.method, headers };
  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = await req.text();
  }
  const res = await fetch(url, init);
  const buf = await res.arrayBuffer();
  const outHeaders = new Headers();
  const contentType = res.headers.get("content-type") || "application/json";
  outHeaders.set("Content-Type", contentType);
  return new NextResponse(buf, { status: res.status, headers: outHeaders });
}

export async function GET(req: NextRequest, ctx: { params: { path: string[] } }) {
  return forward(req, ctx.params.path || []);
}

export async function POST(req: NextRequest, ctx: { params: { path: string[] } }) {
  return forward(req, ctx.params.path || []);
}

export async function OPTIONS() {
  return new NextResponse(null, {
    status: 204,
    headers: {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type, X-Owner-Secret, X-Payout-Session",
    },
  });
}
