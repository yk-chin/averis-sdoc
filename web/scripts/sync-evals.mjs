// Copy the evaluation artefacts from ../evals into public/evals (the PNGs are git-ignored at their source).
import { copyFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
const src = join(process.cwd(), "..", "evals"), dst = join(process.cwd(), "public", "evals");
mkdirSync(dst, { recursive: true });
for (const f of ["metrics_latest.json", "history.jsonl", "perturbation.json", "progress.png", "calibration.png", "threshold.png"]) {
  copyFileSync(join(src, f), join(dst, f)); console.log("copied", f);
}
