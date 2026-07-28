import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, extname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";

const [inputArgument, outputArgument] = process.argv.slice(2);
if (!inputArgument || !outputArgument) {
  console.error("Usage: node scripts/markdown-to-pdf.mjs <input.md> <output.pdf>");
  process.exit(1);
}

const inputPath = resolve(inputArgument);
const outputPath = resolve(outputArgument);
if (!existsSync(inputPath)) {
  console.error(`Markdown file not found: ${inputPath}`);
  process.exit(1);
}

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function inlineMarkdown(value) {
  let html = escapeHtml(value);
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2">$1</a>');
  return html;
}

function renderMarkdown(markdown) {
  const lines = markdown.replaceAll("\r\n", "\n").split("\n");
  const blocks = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    if (line.startsWith("```")) {
      const language = line.slice(3).trim();
      const code = [];
      index += 1;
      while (index < lines.length && !lines[index].startsWith("```")) {
        code.push(lines[index]);
        index += 1;
      }
      index += 1;
      blocks.push(
        `<pre${language ? ` data-language="${escapeHtml(language)}"` : ""}><code>${escapeHtml(code.join("\n"))}</code></pre>`
      );
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      const level = heading[1].length;
      blocks.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
      index += 1;
      continue;
    }

    if (/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
      blocks.push("<hr>");
      index += 1;
      continue;
    }

    const listMatch = line.match(/^\s*(?:([-+*])|(\d+)\.)\s+(.+)$/);
    if (listMatch) {
      const ordered = Boolean(listMatch[2]);
      const tag = ordered ? "ol" : "ul";
      const items = [];
      while (index < lines.length) {
        const item = lines[index].match(/^\s*(?:([-+*])|(\d+)\.)\s+(.+)$/);
        if (!item || Boolean(item[2]) !== ordered) break;
        items.push(`<li>${inlineMarkdown(item[3])}</li>`);
        index += 1;
        while (index < lines.length && /^\s{3,}\S/.test(lines[index])) {
          items[items.length - 1] = items[items.length - 1].replace(
            "</li>",
            ` ${inlineMarkdown(lines[index].trim())}</li>`
          );
          index += 1;
        }
        if (index < lines.length && !lines[index].trim()) {
          const nextNonblank = lines.slice(index).findIndex((candidate) => candidate.trim());
          const candidateIndex = nextNonblank === -1 ? -1 : index + nextNonblank;
          if (
            candidateIndex !== -1 &&
            /^\s*(?:([-+*])|(\d+)\.)\s+(.+)$/.test(lines[candidateIndex])
          ) {
            index = candidateIndex;
          } else {
            break;
          }
        }
      }
      blocks.push(`<${tag}>${items.join("")}</${tag}>`);
      continue;
    }

    const paragraph = [line.trim()];
    index += 1;
    while (
      index < lines.length &&
      lines[index].trim() &&
      !lines[index].startsWith("```") &&
      !/^(#{1,6})\s+/.test(lines[index]) &&
      !/^\s*(?:[-+*]|\d+\.)\s+/.test(lines[index])
    ) {
      paragraph.push(lines[index].trim());
      index += 1;
    }
    blocks.push(`<p>${inlineMarkdown(paragraph.join(" "))}</p>`);
  }

  return blocks.join("\n");
}

const title = basename(inputPath, extname(inputPath)).replaceAll("_", " ");
const content = renderMarkdown(readFileSync(inputPath, "utf8"));
const html = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>${escapeHtml(title)}</title>
<style>
  @page { size: A4; margin: 18mm 17mm 20mm; }
  * { box-sizing: border-box; }
  html { font-family: "Segoe UI", Arial, sans-serif; color: #172033; }
  body { margin: 0 auto; max-width: 178mm; font-size: 10.5pt; line-height: 1.55; }
  h1, h2, h3 { color: #0f2747; break-after: avoid; page-break-after: avoid; }
  h1 { font-size: 26pt; line-height: 1.15; margin: 0 0 16mm; border-bottom: 3px solid #67a552; padding-bottom: 5mm; }
  h2 { font-size: 17pt; line-height: 1.25; margin: 10mm 0 3mm; padding-bottom: 1.5mm; border-bottom: 1px solid #ccd5e1; }
  h3 { font-size: 13pt; line-height: 1.3; margin: 7mm 0 2mm; }
  p { margin: 0 0 3.3mm; orphans: 3; widows: 3; }
  ul, ol { margin: 1mm 0 4mm 6mm; padding-left: 6mm; }
  li { margin: 0 0 1.4mm; padding-left: 1.5mm; }
  pre { white-space: pre-wrap; overflow-wrap: anywhere; background: #f3f6f9; border-left: 3px solid #67a552; border-radius: 3px; padding: 3mm 4mm; margin: 2mm 0 4mm; break-inside: avoid; }
  code { font-family: "Cascadia Mono", Consolas, monospace; font-size: 0.9em; background: #f3f6f9; border-radius: 3px; padding: 0.2em 0.35em; }
  pre code { background: transparent; padding: 0; }
  strong { color: #101827; }
  a { color: #2f6cbd; text-decoration: none; }
  hr { border: 0; border-top: 1px solid #ccd5e1; margin: 7mm 0; }
</style>
</head>
<body>${content}</body>
</html>`;

const temporaryHtml = join(
  tmpdir(),
  `operatoros-${basename(inputPath, extname(inputPath))}-${process.pid}.html`
);
writeFileSync(temporaryHtml, html, "utf8");

const browserCandidates = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
];
const browser = browserCandidates.find(existsSync);
if (!browser) {
  console.error("Microsoft Edge or Google Chrome is required to generate the PDF.");
  process.exit(1);
}

const result = spawnSync(
  browser,
  [
    "--headless",
    "--disable-gpu",
    "--no-pdf-header-footer",
    `--print-to-pdf=${outputPath}`,
    pathToFileURL(temporaryHtml).href,
  ],
  { stdio: "inherit" }
);
if (result.error) throw result.error;
process.exit(result.status ?? 1);
