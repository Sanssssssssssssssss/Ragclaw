import { chromium } from "playwright";

const uiBaseUrl = process.env.UI_BASE_URL || "http://127.0.0.1:3000";
const maxSampleDistance = Number(process.env.UI_MAX_SCROLL_DISTANCE || 180);
const finalDistanceThreshold = Number(process.env.UI_FINAL_SCROLL_DISTANCE || 80);
const sampleCount = Number(process.env.UI_SCROLL_SAMPLES || 28);
const sampleDelayMs = Number(process.env.UI_SCROLL_SAMPLE_DELAY_MS || 250);

/**
 * Returns one scroll-metric object from a Playwright page input and samples the chat scroll container state.
 */
async function collectScrollMetrics(page) {
  return page.locator(".chat-scroll-area").evaluate((node) => ({
    scrollTop: node.scrollTop,
    clientHeight: node.clientHeight,
    scrollHeight: node.scrollHeight,
    distanceToBottom: node.scrollHeight - node.scrollTop - node.clientHeight
  }));
}

/**
 * Returns no explicit value from no inputs and verifies chat scroll stability plus lazy secondary views.
 */
async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 760 } });

  try {
    await page.goto(uiBaseUrl, { waitUntil: "networkidle" });
    await page.waitForFunction(
      () => {
        const textarea = document.querySelector("textarea");
        const sendButton = Array.from(document.querySelectorAll("button")).find(
          (node) => node.textContent?.trim().toLowerCase() === "send"
        );
        return Boolean(textarea && !textarea.hasAttribute("disabled") && sendButton);
      },
      null,
      { timeout: 180000 }
    );

    await page
      .locator("textarea")
      .first()
      .fill("According to the knowledge base, summarize XSS briefly and include file paths.");
    await page.waitForFunction(
      () =>
        Array.from(document.querySelectorAll("button")).some(
          (node) =>
            node.textContent?.trim().toLowerCase() === "send" && !node.hasAttribute("disabled")
        ),
      null,
      { timeout: 30000 }
    );
    await page.locator("textarea").first().press(`${process.platform === "darwin" ? "Meta" : "Control"}+Enter`);

    const scrollSamples = [];
    for (let index = 0; index < sampleCount; index += 1) {
      scrollSamples.push(await collectScrollMetrics(page));
      await page.waitForTimeout(sampleDelayMs);
    }

    await page.waitForFunction(
      () =>
        Array.from(document.querySelectorAll("article")).some((node) => {
          const text = node.textContent || "";
          return text.includes("Input ") && text.includes(" Output ");
        }),
      null,
      { timeout: 180000 }
    );

    await page.getByRole("button", { name: "Trace" }).click();
    await page.waitForFunction(
      () => {
        const text = document.body.textContent || "";
        return text.includes("Every turn // split from main chat") || text.includes("# Trace");
      },
      null,
      { timeout: 30000 }
    );

    await page.getByRole("button", { name: "Tools" }).click();
    await page.getByRole("button", { name: "Open files" }).click();
    await page.waitForFunction(
      () => document.body.textContent?.includes("Workspace editor"),
      null,
      { timeout: 30000 }
    );

    const articles = page.locator("article");
    const articleCount = await articles.count();
    const lastArticleText = await articles.nth(articleCount - 1).innerText();
    const tokenBadgeTexts = await page.locator("article").evaluateAll((nodes) =>
      nodes
        .map((node) => node.textContent || "")
        .filter((text) => text.includes("Input ") && text.includes(" Output "))
    );
    const traceVisible = await page
      .locator("text=Every turn // split from main chat")
      .count();
    const filesVisible = await page.locator("text=Workspace editor").count();

    const maxDistance = Math.max(...scrollSamples.map((item) => item.distanceToBottom));
    const finalDistance = scrollSamples[scrollSamples.length - 1].distanceToBottom;
    const verification = {
      articleCount,
      tokenBadgeTexts,
      lastArticleHasContent: lastArticleText.trim().length > 0,
      hadScrollableOverflow: scrollSamples.some((item) => item.scrollHeight > item.clientHeight),
      maxDistance,
      finalDistance,
      traceVisible: traceVisible > 0,
      filesVisible: filesVisible > 0,
      pass:
        lastArticleText.trim().length > 0 &&
        traceVisible > 0 &&
        filesVisible > 0 &&
        maxDistance <= maxSampleDistance &&
        finalDistance <= finalDistanceThreshold,
      thresholds: {
        maxSampleDistance,
        finalDistanceThreshold
      },
      lastSample: scrollSamples[scrollSamples.length - 1]
    };

    console.log(JSON.stringify(verification, null, 2));

    if (!verification.pass) {
      process.exitCode = 1;
    }
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
