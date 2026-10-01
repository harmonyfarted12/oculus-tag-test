import type { VercelRequest, VercelResponse } from "@vercel/node";

interface VerifyBody {
  attestation_token?: string;
  challenge_nonce?: string;
}

export default async function handler(
  req: VercelRequest,
  res: VercelResponse
) {
  if (req.method !== "POST") {
    return res.status(405).json({
      valid: false,
      error: "Method not allowed"
    });
  }

  try {
    const body = req.body as VerifyBody;

    const token = body?.attestation_token;
    const nonce = body?.challenge_nonce;

    if (!token || !nonce) {
      return res.status(400).json({
        valid: false,
        error: "Missing attestation_token or challenge_nonce"
      });
    }

    // Your Meta access token MUST be stored in Vercel
    // Environment Variables.
    const metaAccessToken = process.env.META_ACCESS_TOKEN;

    if (!metaAccessToken) {
      console.error("META_ACCESS_TOKEN is missing");

      return res.status(500).json({
        valid: false,
        error: "Server configuration error"
      });
    }

    // Ask Meta to verify the attestation token.
    const params = new URLSearchParams();

    params.set("token", token);
    params.set("access_token", metaAccessToken);

    const response = await fetch(
      `https://graph.oculus.com/platform_integrity/verify?${params.toString()}`,
      {
        method: "GET"
      }
    );

    const metaResult = await response.json();

    if (!response.ok) {
      console.error("Meta verification failed:", metaResult);

      return res.status(403).json({
        valid: false,
        error: "Meta rejected the attestation token"
      });
    }

    // Meta should return a successful verification result
    // containing the verified claims.
    const result = metaResult?.data?.[0];

    if (!result || result.message !== "success" || !result.claims) {
      return res.status(403).json({
        valid: false,
        error: "Invalid attestation"
      });
    }

    // The claims returned by Meta are Base64URL encoded.
    const claimsJson = Buffer.from(
      result.claims,
      "base64url"
    ).toString("utf8");

    const claims = JSON.parse(claimsJson);

    // ---------------------------------------------------------
    // Verify the nonce returned inside the Meta claims.
    // ---------------------------------------------------------

    const returnedNonce =
      claims?.request_details?.nonce;

    if (!returnedNonce) {
      return res.status(403).json({
        valid: false,
        error: "Attestation did not contain a nonce"
      });
    }

    if (returnedNonce !== nonce) {
      return res.status(403).json({
        valid: false,
        error: "Nonce mismatch"
      });
    }

    // ---------------------------------------------------------
    // Verify expiration.
    // ---------------------------------------------------------

    const expiration =
      claims?.request_details?.exp;

    if (
      typeof expiration !== "number" ||
      expiration <= Math.floor(Date.now() / 1000)
    ) {
      return res.status(403).json({
        valid: false,
        error: "Attestation expired"
      });
    }

    // ---------------------------------------------------------
    // Successful verification.
    // ---------------------------------------------------------

    return res.status(200).json({
      valid: true,
      message: "Attestation verified"
    });

  } catch (error) {
    console.error("Verification error:", error);

    return res.status(500).json({
      valid: false,
      error: "Internal server error"
    });
  }
}
