import type { Metadata } from "next";
import Link from "next/link";
import styles from "../page.module.css";

export const metadata: Metadata = {
  title: "About · SmashKartz",
};

export default function About() {
  return (
    <main className={styles.main}>
      <header className={styles.hero}>
        <p className={styles.kicker}>About</p>
        <h1 className={styles.title}>
          E-<em>Delta</em>
        </h1>
        <p className={styles.lede}>
          Public telemetry is resampled every 20 m, cut into 30–40 segments, and pushed through a physics
          twin, a rules mask, a two-lap dynamic programme and a short uncertainty-aware beam search. The
          derivative of race-position value with respect to energy is the energy shadow price shown on the
          track map.
        </p>
        <nav className={styles.nav}>
          <Link href="/" className={styles.button}>
            Back home
          </Link>
        </nav>
      </header>
    </main>
  );
}
