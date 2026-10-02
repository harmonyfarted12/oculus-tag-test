import type { VercelRequest, VercelResponse } from "@vercel/node";

export default async function handler(
  req: VercelRequest,
  res: VercelResponse
) {
  if (req.method === "GET") {
    return res.status(200).json({
      success: true,
      message: "Oculus Tag Verify API is online.",
      method: "POST",
      endpoint: "/api/verify"
    });
  }

  if (req.method !== "POST") {
    return res.status(405).json({
      success: false,
      error: "Method Not Allowed"
    });
  }

  const { attestation_token, challenge_nonce } = req.body || {};

  if (!attestation_token) {
    return res.status(400).json({
      success: false,
      error: "Missing attestation_token"
    });
  }

  if (!challenge_nonce) {
    return res.status(400).json({
      success: false,
      error: "Missing challenge_nonce"
    });
  }

  const metaAccessToken = process.env.META_ACCESS_TOKEN;

  if (!metaAccessToken) {
    return res.status(500).json({
      success: false,
      error: "META_ACCESS_TOKEN is not configured"
    });
  }

  try {
    const metaResponse = await fetch(
      "https://graph.oculus.com/platform_integrity/verify?" +
        new URLSearchParams({
          token: attestation_token,
          access_token: metaAccessToken
        })
    );

    const result = await metaResponse.json();

    if (!metaResponse.ok) {
      return res.status(401).json({
        success: false,
        error: "Meta rejected the attestation token"
      });
    }

    return res.status(200).json({
      success: true,
      message: "Oculus Tag attestation verified.",
      meta: result
    });

  } catch {
    return res.status(502).json({
      success: false,
      error: "Could not contact Meta."
    });
  }
}
