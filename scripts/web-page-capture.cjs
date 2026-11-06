#!/usr/bin/env node
// web-page-capture.cjs — screenshot and/or readable text of a web page with the machine's Chrome.
//
//   node web-page-capture.cjs <url> [--shot out.png] [--text out.txt] [--width 1920] [--height 1000]
//        [--dpr 2] [--full] [--remove ".ad,#promo"] [--wait 1500] [--chrome /path/to/chrome]
//        [--pdf out.pdf --paper a4|letter [--landscape]]
//
// Uses puppeteer-core installed in ~/.video-studio/node (install.sh) so no extra browser is downloaded.
// Sizes are CSS pixels; the PNG is width×dpr. --remove hides elements (cookie banners, ads) before the shot.
const path = require("path");
const os = require("os");

const home = process.env.VS_HOME || path.join(os.homedir(), ".video-studio");
let puppeteer;
try {
  puppeteer = require(path.join(home, "node", "node_modules", "puppeteer-core"));
} catch (e) {
  console.error("✗ puppeteer-core missing — run install.sh (or: cd ~/.video-studio/node && npm i puppeteer-core@24)");
  process.exit(2);
}

const args = process.argv.slice(2);
const url = args[0];
const opt = (name, dflt) => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : dflt;
};
const has = (name) => args.includes(name);
const fs = require("fs");
const CANDIDATES = [
  process.env.VS_CHROME,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium", "/usr/bin/chromium-browser",
].filter(Boolean);
const chrome = opt("--chrome", CANDIDATES.find((p) => fs.existsSync(p)) || CANDIDATES[0]);

(async () => {
  const browser = await puppeteer.launch({
    executablePath: chrome,
    headless: true,
    // keep Chrome's sandbox; only root (Linux containers) cannot run with it
    args: ["--hide-scrollbars", "--mute-audio", ...(process.getuid && process.getuid() === 0 ? ["--no-sandbox"] : [])],
  });
  try {
    const page = await browser.newPage();
    await page.setUserAgent(
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36 video-studio"
    );
    await page.setViewport({
      width: +opt("--width", 1920),
      height: +opt("--height", 1000),
      deviceScaleFactor: +opt("--dpr", 2),
    });
    await page.goto(url, { waitUntil: "networkidle2", timeout: 60000 }).catch(async (e) => {
      // Some sites never go network-idle; take what loaded.
      if (!/timeout/i.test(String(e))) throw e;
    });
    await new Promise((r) => setTimeout(r, +opt("--wait", 1500)));
    const remove = opt("--remove", "");
    if (remove) {
      await page.evaluate((sel) => document.querySelectorAll(sel).forEach((el) => el.remove()), remove);
    }
    const meta = await page.evaluate(() => ({
      title: document.title,
      description: (document.querySelector('meta[name="description"],meta[property="og:description"]') || {}).content || "",
      image: (document.querySelector('meta[property="og:image"]') || {}).content || "",
      published: (document.querySelector('meta[property="article:published_time"],time[datetime]') || {}).content ||
        ((document.querySelector("time[datetime]") || {}).dateTime || ""),
    }));
    const shot = opt("--shot");
    if (shot) await page.screenshot({ path: shot, fullPage: has("--full") });
    const textOut = opt("--text");
    if (textOut) {
      const text = await page.evaluate(() => {
        // work on a detached copy so the live page (screenshot, PDF) keeps its nav/footer/forms
        const src = document.querySelector("article") || document.querySelector("main") || document.body;
        const root = src.cloneNode(true);
        root.querySelectorAll("script,style,noscript,nav,footer,aside,form").forEach((n) => n.remove());
        root.style.position = "absolute"; root.style.left = "-99999px";
        document.body.appendChild(root);
        const text = root.innerText.replace(/\n{3,}/g, "\n\n").trim();
        root.remove();
        return text;
      });
      fs.writeFileSync(textOut, text, "utf8");
    }
    const pdf = opt("--pdf");
    if (pdf) {
      await page.pdf({ path: pdf, format: opt("--paper", "a4"), landscape: has("--landscape"), printBackground: true });
    }
    console.log(JSON.stringify(meta));
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error("✗ " + (e && e.message ? e.message : e));
  process.exit(1);
});
