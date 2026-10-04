// Customer wording for v2 in one place. Plain English, no "AI" (customers meet Saathi and our team),
// and errors that say what happened and what to do next.

/** HTTP reason phrases the API client passes through as messages when the server sent no detail. */
const RAW = /^(Internal Server Error|Bad Gateway|Service Unavailable|Gateway Timeout|Failed to fetch|Load failed|NetworkError.*|.*fetch.*failed.*|speech failed.*)$/i;

/** A sentence a person can act on. The server's own 4xx messages are already written for people,
 *  so they pass through; raw status text and network failures become a plain retry message. */
export function friendlyError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error ?? "");
  if (!message.trim() || RAW.test(message.trim())) {
    return "Saathi couldn't do that just now. Please check your connection and try again in a minute.";
  }
  return message;
}

export const ROLE_TEXT = { owner: "Owner", family: "Family", reviewer: "Our team", platform_admin: "Platform admin" } as const;
