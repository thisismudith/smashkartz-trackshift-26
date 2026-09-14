/**
 * Every (track, session) pair the sim index knows, enumerated at BUILD time.
 *
 * `output: "export"` has no server to resolve a dynamic segment on request, so each pair
 * has to be turned into its own directory during the build. This reads the same index the
 * running app reads, which is what keeps the two from drifting: a session that exists at
 * runtime but was never enumerated here is a 404 on a static host.
 *
 * It runs in Node, not the browser, so NEXT_PUBLIC_SIM_BASE must be an ABSOLUTE URL for
 * the build — a relative one has no origin to resolve against outside a page.
 *
 * Shared by both dynamic routes rather than written twice: they cover the same pairs, and
 * two copies would let one of them silently fall behind.
 */
import { defaultSimSource } from "@/sim/data/source";

export async function generateStaticParams() {
  const index = await defaultSimSource.index();
  const out: { track: string; session: string }[] = [];
  for (const [track, sessions] of Object.entries(index.sessions ?? {})) {
    for (const session of Object.keys(sessions)) out.push({ track, session });
  }
  return out;
}
