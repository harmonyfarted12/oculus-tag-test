import type { VercelRequest, VercelResponse } from "@vercel/node";
import crypto from "crypto";

export default function handler(
  req: VercelRequest,
  res: VercelResponse
) {
  if (req.method !== "GET") {
    return res.status(405).json({
      error: "Method not allowed"
    });
  }

  // Generate a cryptographically secure random nonce.
  const nonce = crypto
    .randomBytes(32)
    .toString("base64url");

  return res.status(200).json({
    challenge_nonce: nonce
  });
}
