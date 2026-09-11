import Link from "next/link";
import styles from "./page.module.css";

const MODULES = [
  { name: "Shadow-price map", note: "Silverstone coloured by λ_E", status: "planned" },
  { name: "Rival belief", note: "HMM over CONSERVING / BALANCED / DEPLOYING / DERATING", status: "planned" },
  { name: "Recommendation", note: "Legal-by-construction action + flips-if", status: "planned" },
  { name: "Race simulator", note: "Full-race, per-driver perspective", status: "planned" },
] as const;

export default function Home() {
  return (
    <main className={styles.main}>
      <header className={styles.hero}>
        <p className={styles.kicker}>TrackShift 2026 · PS1</p>
        <h1 className={styles.title}>
          Smash<em>Kartz</em>
        </h1>
        <p className={styles.lede}>
          E-Delta — a segment-level decision engine for where, not just whether, a 2026 F1 car should
          spend or recover electrical energy in a battle.
        </p>
        <nav className={styles.nav}>
          <Link href="/about" className={styles.button}>
            About the project
          </Link>
        </nav>
      </header>

      <section className={styles.grid} aria-label="Modules">
        {MODULES.map((m) => (
          <article key={m.name} className={styles.card}>
            <h2>{m.name}</h2>
            <p>{m.note}</p>
            <span className={styles.badge}>{m.status}</span>
          </article>
        ))}
      </section>

      <footer className={styles.footer}>
        Public telemetry only. Energy state is inferred, never measured — provenance is always shown.
      </footer>
    </main>
  );
}
