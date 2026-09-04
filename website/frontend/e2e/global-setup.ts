import { existsSync, mkdirSync, rmSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const DATA_DIR = path.join(HERE, '.e2e')

/**
 * Delete the throwaway database before anything opens it.
 *
 * Playwright runs `globalSetup` before `webServer`, which is the only moment
 * this is safe: the seeder runs per test, by which time Flask is holding the
 * file handle.
 *
 * A stale database is not a hypothetical -- the developer's `data/zephyr.db`
 * was five migrations behind, so every guild endpoint answered 500 with
 * "no such column: guilds.tts_language" and the suite reported it as a missing
 * element, three layers from the cause. A fresh file every run means the schema
 * is always the current one, which is also what makes the E2E suite a check on
 * the *models* and not on whatever was lying around.
 */
export default function globalSetup() {
  mkdirSync(DATA_DIR, { recursive: true })
  for (const name of ['zephyr.db', 'zephyr.db-wal', 'zephyr.db-shm']) {
    const file = path.join(DATA_DIR, name)
    if (existsSync(file)) rmSync(file)
  }
}
