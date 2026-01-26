import express from "express";
import fs from "fs";
import path from "path";

const app = express();
app.use(express.json({ type: ["application/json"] }));

const VERIFY_TOKEN = process.env.META_VERIFY_TOKEN || "";
const PAGE_ACCESS_TOKEN = process.env.PAGE_ACCESS_TOKEN || "";

const IG_BUSINESS_ID = process.env.IG_BUSINESS_ID || "17841400435126539";
const TARGET_MEDIA_ID = process.env.TARGET_MEDIA_ID || "18049243130410971";
const KEYWORD = (process.env.KEYWORD || "Подкаст").toLowerCase();
const PODCAST_LINK =
  process.env.PODCAST_LINK ||
  "https://podcasts.apple.com/fr/podcast/innerfrench/id1231472946";

const POLL_INTERVAL_SEC = Math.max(10, Number(process.env.POLL_INTERVAL_SEC || "20"));
const PRIVATE_REPLY_MAX_AGE_SEC = Math.max(
  60,
  Number(process.env.PRIVATE_REPLY_MAX_AGE_SEC || "900") // 15 minutes
);

// In-memory dedupe for DM mids
const seenMessageMids = new Set();

// Persistent dedupe for comment IDs
const DATA_DIR = "/app/data";
const SEEN_COMMENTS_FILE = path.join(DATA_DIR, "seen_comments.json");
let seenCommentIds = new Set();

try {
  fs.mkdirSync(DATA_DIR, { recursive: true });
  const raw = fs.readFileSync(SEEN_COMMENTS_FILE, "utf8");
  seenCommentIds = new Set(JSON.parse(raw));
  console.log(`Loaded ${seenCommentIds.size} seen comment IDs`);
} catch {
  console.log("No seen_comments.json yet (first run is fine).");
}

function persistSeenComments() {
  try {
    fs.writeFileSync(SEEN_COMMENTS_FILE, JSON.stringify([...seenCommentIds]));
  } catch (e) {
    console.error("Failed to persist seen comments:", e);
  }
}

// --- Webhook verify
app.get("/webhook", (req, res) => {
  const mode = req.query["hub.mode"];
  const token = req.query["hub.verify_token"];
  const challenge = req.query["hub.challenge"];

  if (mode === "subscribe" && token === VERIFY_TOKEN) {
    return res.status(200).send(challenge);
  }
  return res.sendStatus(403);
});

// --- Webhook receiver (DM only)
app.post("/webhook", async (req, res) => {
  res.sendStatus(200);

  try {
    const body = req.body;
    console.log("Webhook event:", JSON.stringify(body));

    const entry = body?.entry?.[0];
    const evt = entry?.messaging?.[0];
    if (!evt) return;

    if (evt.read || evt.reaction) return;
    if (evt?.message?.is_echo) return;

    const senderId = evt?.sender?.id;
    const text = evt?.message?.text;
    const mid = evt?.message?.mid;

    if (!senderId || !text) return;
    if (senderId === IG_BUSINESS_ID) return;

    if (mid && seenMessageMids.has(mid)) return;
    if (mid) seenMessageMids.add(mid);

    if (!text.toLowerCase().includes(KEYWORD)) return;

    const replyText =
      `Вот ссылка на подкаст: ${PODCAST_LINK}\n\n` +
      `Пользуйтесь в удовольствие 🇫🇷✨`;

    await sendDmToUserId({ recipientId: senderId, text: replyText });
    console.log("✅ DM replied to:", senderId);
  } catch (err) {
    console.error("Webhook handler error:", err);
  }
});

app.get("/health", (_req, res) => res.status(200).send("ok"));

// --- Send DM (normal)
async function sendDmToUserId({ recipientId, text }) {
  if (!PAGE_ACCESS_TOKEN) throw new Error("Missing PAGE_ACCESS_TOKEN");

  const url = `https://graph.facebook.com/v24.0/me/messages?access_token=${encodeURIComponent(
    PAGE_ACCESS_TOKEN
  )}`;

  const payload = {
    recipient: { id: recipientId },
    message: { text },
  };

  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  const raw = await resp.text();
  if (!resp.ok) throw new Error(`Send DM failed ${resp.status}: ${raw}`);

  console.log("Send DM response:", raw);
  return JSON.parse(raw);
}

// --- Private reply DM to comment
async function sendPrivateReplyToComment({ commentId, text }) {
  if (!PAGE_ACCESS_TOKEN) throw new Error("Missing PAGE_ACCESS_TOKEN");

  const url = `https://graph.facebook.com/v24.0/me/messages?access_token=${encodeURIComponent(
    PAGE_ACCESS_TOKEN
  )}`;

  const payload = {
    recipient: { comment_id: commentId },
    message: { text },
  };

  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  const raw = await resp.text();
  if (!resp.ok) throw new Error(`Private reply failed ${resp.status}: ${raw}`);

  console.log("Private reply response:", raw);
  return JSON.parse(raw);
}

// --- Poll comments
async function fetchRecentComments(mediaId) {
  if (!PAGE_ACCESS_TOKEN) throw new Error("Missing PAGE_ACCESS_TOKEN");

  const url = `https://graph.facebook.com/v24.0/${mediaId}/comments?fields=id,text,timestamp&limit=50&access_token=${encodeURIComponent(
    PAGE_ACCESS_TOKEN
  )}`;

  const resp = await fetch(url);
  const raw = await resp.text();
  if (!resp.ok) throw new Error(`Fetch comments failed ${resp.status}: ${raw}`);

  const data = JSON.parse(raw);
  return Array.isArray(data?.data) ? data.data : [];
}

function parseUnixSeconds(ts) {
  const ms = Date.parse(ts);
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : null;
}

async function pollCommentsOnce() {
  try {
    const nowSec = Math.floor(Date.now() / 1000);
    const comments = await fetchRecentComments(TARGET_MEDIA_ID);

    console.log(`[poll] fetched ${comments.length} comments for media ${TARGET_MEDIA_ID}`);

    for (const c of comments.slice(0, 3)) {
      console.log(
        `[poll] top: id=${c?.id} ts=${c?.timestamp} text="${(c?.text || "").slice(0, 60)}"`
      );
    }

    for (const c of comments) {
      const commentId = c?.id;
      const text = (c?.text || "").toString();
      const ts = c?.timestamp;

      if (!commentId || !text) continue;
      if (seenCommentIds.has(commentId)) continue;
      if (!text.toLowerCase().includes(KEYWORD)) continue;

      const createdSec = ts ? parseUnixSeconds(ts) : null;
      const ageSec = createdSec ? nowSec - createdSec : null;

      console.log(`[poll] keyword match id=${commentId} ageSec=${ageSec}`);

      // Policy window for private replies
      if (ageSec !== null && ageSec > PRIVATE_REPLY_MAX_AGE_SEC) {
        console.log(`[poll] skipping old comment id=${commentId} ageSec=${ageSec}`);
        seenCommentIds.add(commentId);
        persistSeenComments();
        continue;
      }

      // Mark as seen before sending (prevents duplicates)
      seenCommentIds.add(commentId);
      persistSeenComments();

      const dmText =
        `Вот ссылка на подкаст: ${PODCAST_LINK}\n\n` +
        `Пользуйтесь в удовольствие 🇫🇷✨`;

      try {
        await sendPrivateReplyToComment({ commentId, text: dmText });
        console.log("✅ Comment->DM sent for comment:", commentId);
      } catch (e) {
        console.error("Private reply failed for comment:", commentId, e?.message || e);
      }
    }
  } catch (e) {
    console.error("Comment polling error:", e);
  }
}

// Start polling
if (TARGET_MEDIA_ID && PAGE_ACCESS_TOKEN) {
  console.log(
    `Starting comment polling for media ${TARGET_MEDIA_ID} every ${POLL_INTERVAL_SEC}s (maxAge=${PRIVATE_REPLY_MAX_AGE_SEC}s)`
  );
  pollCommentsOnce();
  setInterval(pollCommentsOnce, POLL_INTERVAL_SEC * 1000);
} else {
  console.log("Comment polling disabled (missing TARGET_MEDIA_ID or PAGE_ACCESS_TOKEN)");
}

const port = process.env.PORT || 3000;
app.listen(port, () => console.log(`Server listening on ${port}`));
