import { useEffect, useState } from "react";
import { api } from "./api";
import { isValidCertId } from "./cert";

/** The Safe Assistant's REAL public certification. */
export interface AssistantCert {
  grade: string;
  cert_id: string;
}

/* ============================================================================
   Single source of truth for the Safe Assistant's public safety grade.

   The landing page ("passed our Safety Battery — Grade A verified") and the
   assistant page ("certification pending") used to derive this independently,
   which let them disagree. Both now read this one hook, backed by the single
   public endpoint GET /api/public/assistant/certification. A grade is returned
   ONLY when a real, verifiable certificate backs it (never a placeholder);
   otherwise null → both surfaces show the honest "grade to come" message.
   ========================================================================== */
export function useAssistantCert(): AssistantCert | null {
  const [cert, setCert] = useState<AssistantCert | null>(null);
  useEffect(() => {
    let alive = true;
    api.assistantCertification()
      .then((c) => {
        if (alive && c?.grade && isValidCertId(c?.cert_id)) {
          setCert({ grade: String(c.grade), cert_id: c.cert_id });
        }
      })
      .catch(() => { /* no cert / offline → gradeless (honest) */ });
    return () => { alive = false; };
  }, []);
  return cert;
}
