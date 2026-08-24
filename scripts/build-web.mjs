import { build } from "esbuild";
import { copyFile, mkdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const output = path.join(root, "src", "confquiz", "static", "assets");
await rm(output, { recursive: true, force: true });
await mkdir(path.join(output, "licenses"), { recursive: true });

await build({
  entryPoints: {
    attendee: path.join(root, "web_src", "attendee.js"),
    presenter: path.join(root, "web_src", "presenter.js"),
  },
  outdir: output,
  bundle: true,
  format: "esm",
  splitting: true,
  chunkNames: "chunks/[name]-[hash]",
  sourcemap: false,
  minify: true,
  target: ["es2022"],
  platform: "browser",
  logLevel: "info",
});

for (const file of ["common.css", "attendee.css", "presenter.css"]) {
  await copyFile(path.join(root, "web_src", file), path.join(output, file));
}

await copyFile(
  path.join(root, "node_modules", "pdfjs-dist", "build", "pdf.worker.min.mjs"),
  path.join(output, "pdf.worker.min.mjs"),
);
await copyFile(
  path.join(root, "node_modules", "bootstrap-icons", "bootstrap-icons.svg"),
  path.join(output, "bootstrap-icons.svg"),
);

const licenses = [
  ["pdfjs-dist", "LICENSE", "PDFJS-LICENSE.txt"],
  ["bootstrap-icons", "LICENSE", "BOOTSTRAP-ICONS-LICENSE.txt"],
];
for (const [packageName, sourceName, targetName] of licenses) {
  await copyFile(
    path.join(root, "node_modules", packageName, sourceName),
    path.join(output, "licenses", targetName),
  );
}
await copyFile(
  path.join(root, "node_modules", "pdfjs-dist", "LICENSE"),
  path.join(output, "licenses", "FIREBASE-APACHE-2.0-LICENSE.txt"),
);
await writeFile(
  path.join(output, "licenses", "FIREBASE-NOTICE.txt"),
  "Firebase JavaScript SDK 12.17.1 is licensed under the Apache License, Version 2.0.\n" +
    "Source: https://github.com/firebase/firebase-js-sdk\n",
  "utf8",
);
await copyFile(
  path.join(root, "THIRD_PARTY_NOTICES.md"),
  path.join(output, "licenses", "THIRD_PARTY_NOTICES.md"),
);
