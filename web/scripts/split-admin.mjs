// After `vite build`, make dist-admin/: the same assets, with the portal (admin.html)
// as the start page, so the anvaya-admin site serves the portal at "/".
import { cpSync, renameSync, rmSync } from "node:fs";

rmSync("dist-admin", { recursive: true, force: true });
cpSync("dist", "dist-admin", { recursive: true });
rmSync("dist-admin/index.html");
renameSync("dist-admin/admin.html", "dist-admin/index.html");
rmSync("dist-admin/manifest.webmanifest", { force: true });
rmSync("dist-admin/fonts", { recursive: true, force: true }); // only the app uses them
// The app's other pages and files: the portal mustn't serve them (v3.html is a whole owner app).
rmSync("dist-admin/v3.html", { force: true });
rmSync("dist-admin/demo", { recursive: true, force: true });
rmSync("dist-admin/pcm-recorder.js", { force: true });
