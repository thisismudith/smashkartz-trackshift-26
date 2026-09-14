import type { Metadata } from "next";
import TelemetryView from "../TelemetryView";

export { generateStaticParams } from "../../../staticParams";
import { defaultSimSource } from "@/sim/data/source";
import type { Catalogue } from "@/sim/data/catalogue";
import { driverStyles } from "@/sim/data/catalogue";
import Link from "next/link";
import s from "../session.module.css";

export const metadata: Metadata = { title: "Telemetry comparison · SmashKartz" };

export default async function TelemetryPage({
  params,
}: {
  params: Promise<{ track: string; session: string }>;
}) {
  const { track, session: encodedSession } = await params;
  const session = decodeURIComponent(encodedSession);
  try {
    const [manifest, cat] = await Promise.all([
      defaultSimSource.sessionManifest({ trackSlug: track, session }),
      defaultSimSource.catalogue<Catalogue>(),
    ]);
    const drivers = [...manifest.drivers].sort((a, b) => (a.laps[0]?.pos ?? 999) - (b.laps[0]?.pos ?? 999));
    const styles = driverStyles(cat.teams, drivers);
    return (
      <main className={s.main}>
        <p className={s.kicker}><Link href={`/sessions/${track}/${encodeURIComponent(session)}`}>Sessions</Link> · telemetry comparison</p>
        <h1 className={s.title}>Shared distance telemetry</h1>
        <p className={s.lede}>Driver A/B traces use the same circuit station axis. Channels remain labelled observed or derived at the point of use.</p>
        {/* `styles` is the plain Record, NOT a lookup function.
            A function cannot cross the server/client boundary -- Next cannot serialise
            one -- so passing `styleOf={(d) => ...}` threw on every render and this page
            has only ever shown the catch branch below. The client component derives its
            own `styleOf` from this map. */}
        <TelemetryView
          track={track}
          session={session}
          manifest={manifest}
          drivers={drivers}
          styles={styles}
        />
      </main>
    );
  } catch (error) {
    return <main className={s.main}><p className={s.lede}>Telemetry is unavailable for this session: {error instanceof Error ? error.message : String(error)}.</p></main>;
  }
}
