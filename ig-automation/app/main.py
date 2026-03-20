import os
import json
import time
import threading
import requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# Configuration from environment variables
VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "my_secure_verify_token_123")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN")
MESSAGE_ACCESS_TOKEN = os.getenv("META_DEV_MESSAGE_TOKEN") or PAGE_ACCESS_TOKEN
PAGE_ID = os.getenv("PAGE_ID")
IG_BUSINESS_ID = os.getenv("IG_BUSINESS_ID")
TARGET_MEDIA_ID = os.getenv("TARGET_MEDIA_ID")
KEYWORD = os.getenv("KEYWORD", "INFO").lower()
TELEGRAM_LINK = os.getenv("TELEGRAM_LINK", "https://t.me/your_channel")
STRICT_FOLLOW_CHECK = os.getenv("STRICT_FOLLOW_CHECK", "false").lower() == "true"

POLL_INTERVAL_SEC = max(10, int(os.getenv("POLL_INTERVAL_SEC", "20")))
PRIVATE_REPLY_MAX_AGE_SEC = max(60, int(os.getenv("PRIVATE_REPLY_MAX_AGE_SEC", "900")))

# Graph API base URL
GRAPH_API_URL = "https://graph.facebook.com/v24.0"

# Track seen messages and comments to avoid duplicates
seen_message_mids = set()
seen_comment_ids = set()

# File paths for persistent storage
DATA_DIR = "/app/data"
SEEN_COMMENTS_FILE = os. path.join(DATA_DIR, "seen_comments.json")


def load_persistent_data():
    """Load previously seen data from files"""
    global seen_comment_ids
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            with open(SEEN_COMMENTS_FILE, "r") as f:
                seen_comment_ids = set(json.load(f))
            print(f"Loaded {len(seen_comment_ids)} seen comment IDs")
        except FileNotFoundError:
            print("No seen_comments. json yet (first run).")
    except Exception as e:
        print(f"Error loading persistent data: {e}")


def persist_seen_comments():
    """Save seen comment IDs to file"""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(SEEN_COMMENTS_FILE, "w") as f:
            json.dump(list(seen_comment_ids), f)
    except Exception as e:
        print(f"Failed to persist seen comments: {e}")


def send_dm_to_user(recipient_id, text):
    """Send a DM to a user using their ID (same as Node.js bot)"""
    if not MESSAGE_ACCESS_TOKEN:
        raise Exception("Missing MESSAGE_ACCESS_TOKEN")

    url = f"{GRAPH_API_URL}/me/messages?access_token={MESSAGE_ACCESS_TOKEN}"
    
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text}
    }
    
    headers = {"Content-Type": "application/json"}
    
    response = requests.post(url, json=payload, headers=headers)
    
    print(f"Send DM response:  {response.status_code} - {response.text}")
    
    if not response.ok:
        raise Exception(f"Send DM failed {response.status_code}:  {response.text}")
    
    return response.json()


def send_private_reply_to_comment(comment_id, text):
    """Send a private reply DM to a comment author (same as Node.js bot)"""
    if not PAGE_ACCESS_TOKEN:
        raise Exception("Missing PAGE_ACCESS_TOKEN")

    url = f"{GRAPH_API_URL}/me/messages?access_token={PAGE_ACCESS_TOKEN}"
    
    payload = {
        "recipient": {"comment_id": comment_id},
        "message": {"text": text}
    }
    
    headers = {"Content-Type": "application/json"}
    
    response = requests.post(url, json=payload, headers=headers)
    
    print(f"Private reply response: {response.status_code} - {response.text}")
    
    if not response.ok:
        raise Exception(f"Private reply failed {response.status_code}: {response.text}")
    
    return response.json()


def check_if_user_follows(user_id):
    """
    Check if a user follows the Instagram account. 
    Uses the Instagram Graph API to check follower status.
    """
    if not MESSAGE_ACCESS_TOKEN or not IG_BUSINESS_ID:
        print("Missing credentials for follower check, assuming follower")
        return True
    
    try:
        # Try to get user info - if they follow us, we can see more info
        url = f"{GRAPH_API_URL}/{user_id}"
        params = {
            "fields": "id,username,name,is_user_follow_business,is_business_follow_user",
            "access_token": MESSAGE_ACCESS_TOKEN
        }
        
        response = requests.get(url, params=params)
        
        if response.ok:
            data = response.json()
            print(f"User data for {user_id}: {data}")
            
            # Check if user follows us
            is_follower = data.get("is_user_follow_business", False)
            return is_follower
        else:
            print(f"Follower check failed: {response.status_code} - {response.text}")
            try:
                err = response.json().get("error", {})
                if err.get("code") == 200:
                    # Missing advanced access cannot be treated as "not following".
                    return None
            except Exception:
                pass

            # Non-permission API errors are treated as unknown follower status.
            return None
            
    except Exception as e:
        print(f"Error checking follower status: {e}")
        return None


def send_initial_message(recipient_id):
    """Send the initial welcome message asking to follow and reply YES"""
    message = """Привет!

Чтобы получить доступ к эксклюзивному контенту:

1️⃣ Подпишись на мою страницу

2️⃣ Ответь «смайлик» на это сообщение

⚠️ Ссылка действует 12 часов, так что не упусти шанс!"""
    
    try:
        send_dm_to_user(recipient_id, message)
        print(f"✅ Initial message sent to {recipient_id}")
    except Exception as e:
        print(f"Error sending initial message: {e}")


def send_not_following_message(recipient_id):
    """Send message when user is not following"""
    message = """Я не вижу твою подписку 💔

Подпишись на мою страницу и снова ответь «смайлик»!"""
    
    try: 
        send_dm_to_user(recipient_id, message)
        print(f"✅ Not following message sent to {recipient_id}")
    except Exception as e: 
        print(f"Error sending not following message: {e}")


def send_success_message(recipient_id):
    """Send success message with Telegram link"""
    message = f"""Готово, поздравляю 🇫🇷✨

Вот твой эксклюзивный доступ: 
{TELEGRAM_LINK}

⏳ Помни, ссылка действует 12 часов. 
Успей воспользоваться!"""
    
    try:
        send_dm_to_user(recipient_id, message)
        print(f"✅ Success message sent to {recipient_id}")
    except Exception as e:
        print(f"Error sending success message: {e}")


def send_initial_message_via_comment(comment_id):
    """Send the initial welcome message as a private reply to a comment"""
    message = """Привет!

Чтобы получить доступ к эксклюзивному контенту:

1️⃣ Подпишись на мою страницу

2️⃣ Ответь «смайлик» на это сообщение

⚠️ Ссылка действует 12 часов, так что не упусти шанс!"""
    
    try:
        send_private_reply_to_comment(comment_id, message)
        print(f"✅ Initial message sent via comment reply:  {comment_id}")
    except Exception as e:
        print(f"Error sending private reply: {e}")


def fetch_recent_comments(media_id):
    """Fetch recent comments from a specific media post"""
    if not PAGE_ACCESS_TOKEN:
        raise Exception("Missing PAGE_ACCESS_TOKEN")

    url = f"{GRAPH_API_URL}/{media_id}/comments"
    params = {
        "fields": "id,text,timestamp",
        "limit": 50,
        "access_token":  PAGE_ACCESS_TOKEN
    }
    
    response = requests. get(url, params=params)
    
    if not response.ok:
        raise Exception(f"Fetch comments failed {response.status_code}: {response.text}")
    
    data = response.json()
    return data. get("data", [])


def poll_comments_once():
    """Poll for new comments containing the keyword"""
    global seen_comment_ids
    
    try:
        now_sec = int(time.time())
        comments = fetch_recent_comments(TARGET_MEDIA_ID)
        
        print(f"[poll] fetched {len(comments)} comments for media {TARGET_MEDIA_ID}")
        
        for c in comments[: 3]: 
            print(f"[poll] top:  id={c. get('id')} ts={c.get('timestamp')} text=\"{(c.get('text') or '')[:60]}\"")
        
        for comment in comments:
            comment_id = comment.get("id")
            text = str(comment.get("text", ""))
            timestamp = comment.get("timestamp")
            
            if not comment_id or not text:
                continue
            
            if comment_id in seen_comment_ids:
                continue
            
            if KEYWORD not in text.lower():
                continue
            
            created_sec = None
            if timestamp:
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(timestamp. replace("Z", "+00:00"))
                    created_sec = int(dt.timestamp())
                except:
                    pass
            
            age_sec = (now_sec - created_sec) if created_sec else None
            
            print(f"[poll] keyword match id={comment_id} ageSec={age_sec}")
            
            if age_sec is not None and age_sec > PRIVATE_REPLY_MAX_AGE_SEC:
                print(f"[poll] skipping old comment id={comment_id} ageSec={age_sec}")
                seen_comment_ids.add(comment_id)
                persist_seen_comments()
                continue
            
            seen_comment_ids.add(comment_id)
            persist_seen_comments()
            
            # Send initial message asking to follow and reply YES
            send_initial_message_via_comment(comment_id)
    
    except Exception as e: 
        print(f"Comment polling error: {e}")


def start_polling():
    """Start comment polling loop"""
    def comment_poll_loop():
        while True:
            poll_comments_once()
            time.sleep(POLL_INTERVAL_SEC)
    
    if TARGET_MEDIA_ID and PAGE_ACCESS_TOKEN:
        print(f"Starting comment polling for media {TARGET_MEDIA_ID} every {POLL_INTERVAL_SEC}s")
        poll_comments_once()
        comment_thread = threading.Thread(target=comment_poll_loop, daemon=True)
        comment_thread.start()
    else:
        print("Comment polling disabled (missing TARGET_MEDIA_ID or PAGE_ACCESS_TOKEN)")


@app.route("/webhook", methods=["GET"])
def verify_webhook():
    """Handle webhook verification from Meta"""
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    
    print(f"Webhook verification:  mode={mode}, token={token}")
    
    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("Webhook verified successfully!")
        return challenge, 200
    else: 
        print(f"Webhook verification failed.  Expected: {VERIFY_TOKEN}, Got: {token}")
        return "Forbidden", 403


@app.route("/webhook", methods=["POST"])
def handle_webhook():
    """Handle incoming webhook events for DMs"""
    data = request.get_json()
    print(f"📨 Webhook event received: {json.dumps(data)}")
    
    try:
        # Handle both Instagram and Page webhook formats
        entries = data.get("entry", [])
        
        for entry in entries:
            # Check for messaging events (DMs)
            messaging_events = entry.get("messaging", [])
            
            for event in messaging_events: 
                # Skip read receipts and reactions
                if event.get("read") or event.get("reaction"):
                    continue
                
                message = event.get("message", {})
                
                # Skip echo messages (messages sent by us)
                if message.get("is_echo"):
                    continue
                
                sender_id = event.get("sender", {}).get("id")
                text = message.get("text", "")
                mid = message.get("mid")
                
                if not sender_id or not text: 
                    continue
                
                # Skip if sender is our own account
                if sender_id == IG_BUSINESS_ID or sender_id == PAGE_ID:
                    continue
                
                # Skip duplicate messages
                if mid and mid in seen_message_mids:
                    continue
                if mid: 
                    seen_message_mids.add(mid)
                
                print(f"📩 DM received from {sender_id}:  {text}")
                
                # Check for смайлик  response (case insensitive, multiple languages)
                text_clean = text.upper().strip()
                if text_clean in ["СМАЙЛИК"]:
                    print(f"✅ смайлик detected from {sender_id}, checking follower status...")
                    
                    is_follower = check_if_user_follows(sender_id)
                    
                    if is_follower is True:
                        print(f"✅ User {sender_id} IS a follower!  Sending success message.")
                        send_success_message(sender_id)
                    elif is_follower is False:
                        print(f"❌ User {sender_id} is NOT a follower.  Sending reminder.")
                        send_not_following_message(sender_id)
                    else:
                        if STRICT_FOLLOW_CHECK:
                            print(f"⚠️ Could not verify follower status for {sender_id}. Strict check enabled, sending reminder.")
                            send_not_following_message(sender_id)
                        else:
                            print(f"⚠️ Could not verify follower status for {sender_id}. Sending success message by fallback.")
                            send_success_message(sender_id)
                    
                    return jsonify({"status": "ok"}), 200
                
                # Check for keyword in DM (same as comment)
                if KEYWORD in text. lower():
                    print(f"🔑 Keyword '{KEYWORD}' found in DM from {sender_id}")
                    send_initial_message(sender_id)
                    return jsonify({"status": "ok"}), 200
    
    except Exception as e: 
        print(f"Webhook handler error: {e}")
    
    return jsonify({"status": "ok"}), 200


@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "service": "ig-automation-bot",
        "seen_comments":  len(seen_comment_ids),
        "seen_messages": len(seen_message_mids)
    }), 200


# Initialize on startup
load_persistent_data()

if __name__ == "__main__":
    start_polling()
    app.run(host="0.0.0.0", port=5000, debug=False)