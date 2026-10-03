// After `vite build`, make dist-admin/: the same assets, with the portal (admin.html)
// as the start page, so the anvaya-admin site serves the portal at "/".
import { cpSync, renameSync, rmSync } from "node:fs";

rmSync("dist-admin", { recursive: true, force: true });
cpSync("dist", "dist-admin", { recursive: true });
rmSync("dist-admin/index.html");
renameSync("dist-admin/admin.html", "dist-admin/index.html");
rmSync("dist-admin/manifest.webmanifest", { force: true });
