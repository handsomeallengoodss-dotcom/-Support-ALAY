
import os
import io
import json
import threading
import discord

from dotenv import load_dotenv
from flask import Flask, render_template_string, request, jsonify, redirect
from flask_login import (
    LoginManager, UserMixin, login_user,
    login_required, logout_user, current_user
)
from werkzeug.utils import secure_filename

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
SECRET_KEY = os.getenv("SECRET_KEY")

if not TOKEN or not ADMIN_PASSWORD or not SECRET_KEY:
    raise RuntimeError("請檢查 .env 的 BOT_TOKEN、ADMIN_PASSWORD、SECRET_KEY")


DATA_DIR = os.environ.get("DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)
CONFIG_FILE = os.path.join(DATA_DIR, "moderation_config.json")

# =========================
# Discord
# =========================

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = discord.Client(intents=intents)

# =========================
# Flask
# =========================


app = Flask(__name__)

app.config.update(
    SECRET_KEY=SECRET_KEY,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=os.environ.get("RAILWAY_ENVIRONMENT") is not None,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=3600,
    MAX_CONTENT_LENGTH=8 * 1024 * 1024
)


login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "home"


class Admin(UserMixin):
    id = "admin"


@login_manager.user_loader
def load_user(user_id):
    return Admin() if user_id == "admin" else None


# =========================
# 自動踢人設定
# =========================

def load_config():
    default = {
        "enabled": False,
        "guild_id": None,
        "channel_id": None,
        "exempt_role_ids": []
    }

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        default.update(data)

        default["exempt_role_ids"] = [
            int(x) for x in default.get("exempt_role_ids", [])
        ]

    except (OSError, ValueError, TypeError):
        pass

    return default


moderation_config = load_config()


def save_config():
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(moderation_config, f, ensure_ascii=False, indent=4)


# =========================
# Discord 輔助函式
# =========================

def get_text_channel(channel_id):
    try:
        channel = bot.get_channel(int(channel_id))
    except (TypeError, ValueError):
        return None

    if isinstance(channel, discord.TextChannel):
        return channel

    return None


def run_async(coro, timeout=30):
    if not bot.is_ready():
        raise RuntimeError("機器人尚未上線")

    future = __import__("asyncio").run_coroutine_threadsafe(
        coro, bot.loop
    )
    return future.result(timeout=timeout)


def message_data(message):
    return {
        "id": str(message.id),
        "author": str(message.author),
        "display_name": getattr(
            message.author, "display_name", str(message.author)
        ),
        "avatar": message.author.display_avatar.url,
        "content": message.content or "",
        "created_at": message.created_at.isoformat(),
        "is_bot": message.author.bot,
        "attachments": [
            {
                "url": a.url,
                "filename": a.filename,
                "content_type": a.content_type or ""
            }
            for a in message.attachments
        ]
    }


async def read_messages(channel, after=None):
    result = []

    kwargs = {
        "limit": 100,
        "oldest_first": True
    }

    if after:
        kwargs["after"] = discord.Object(id=int(after))

    async for msg in channel.history(**kwargs):
        result.append(message_data(msg))

    return result


# =========================
# 網頁
# =========================

HTML = r"""
<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Support ALAY 控制中心</title>

<style>
*{box-sizing:border-box}
body{
    margin:0;
    background:#202225;
    color:white;
    font-family:Arial,"Microsoft JhengHei",sans-serif
}
.panel{
    max-width:1200px;
    margin:20px auto;
    padding:20px;
    background:#2f3136;
    border-radius:12px
}
h2{color:#9aa8ff}
.section{
    background:#36393f;
    padding:16px;
    margin:16px 0;
    border-radius:10px
}
input,select,textarea,button{
    width:100%;
    padding:11px;
    margin:6px 0;
    border:0;
    border-radius:7px;
    font:inherit
}
input,select,textarea{
    background:#40444b;
    color:white
}
button{
    background:#5865f2;
    color:white;
    font-weight:bold;
    cursor:pointer
}
button.gray{background:#747f8d}
.note{color:#bbb;font-size:13px;line-height:1.6}
.status{color:#43b581}
.chat-layout{
    display:grid;
    grid-template-columns:220px minmax(0,1fr);
    gap:12px
}
.sidebar,.chat-main{
    background:#202225;
    padding:12px;
    border-radius:8px;
    min-width:0
}
.chat-main{
    display:flex;
    flex-direction:column;
    min-height:520px
}
.chat-title{
    padding:12px;
    border-bottom:1px solid #444
}
.chat-log{
    flex:1;
    min-height:300px;
    max-height:600px;
    overflow:auto;
    padding:10px
}
.msg{
    display:flex;
    gap:10px;
    padding:9px 3px
}
.avatar{
    width:38px;
    height:38px;
    border-radius:50%;
    object-fit:cover
}
.msg-body{min-width:0;flex:1}
.msg-head{display:flex;gap:10px;flex-wrap:wrap}
.msg-name{font-weight:bold}
.msg-time{color:#999;font-size:11px}
.msg-content{
    white-space:pre-wrap;
    overflow-wrap:anywhere;
    margin-top:5px
}
.msg-image{
    max-width:min(400px,100%);
    max-height:300px;
    display:block;
    margin-top:8px;
    border-radius:8px
}
.composer{border-top:1px solid #444;padding-top:10px}
.composer textarea{min-height:65px}
.result{white-space:pre-wrap;font-size:13px}
@media(max-width:650px){
    .panel{margin:0;border-radius:0;padding:12px}
    .chat-layout{grid-template-columns:1fr}
}
</style>
</head>

<body>
<div class="panel">
<h2>Support ALAY 控制中心</h2>

{% if not logged_in %}

<h3>管理員登入</h3>
<form method="POST" action="/login">
<input type="password" name="password" placeholder="管理員密碼" required>
<button type="submit">登入</button>
</form>

{% else %}

<p class="status">● 已登入管理後台</p>
<p id="botStatus">正在連接機器人...</p>

<div class="section">
<h3>Discord 即時聊天室</h3>
<p class="note">選擇伺服器和頻道，查看訊息並以機器人身分發言。</p>

<div class="chat-layout">
<div class="sidebar">
<label>聊天室伺服器</label>
<select id="chatGuild"></select>

<label>聊天室頻道</label>
<select id="chatChannel"></select>

<button class="gray" onclick="loadMessages()">重新整理</button>
<p class="note" id="chatStatus"></p>
</div>

<div class="chat-main">
<div class="chat-title" id="chatTitle">尚未選擇頻道</div>
<div class="chat-log" id="chatLog">請選擇頻道</div>

<div class="composer">
<textarea id="message" placeholder="輸入訊息..."></textarea>
<button onclick="sendMessage()">發送訊息</button>
<button class="gray" onclick="document.getElementById('image').click()">傳送圖片</button>
<input type="file" id="image" accept="image/*" style="display:none" onchange="sendImage()">
<p class="note" id="sendStatus"></p>
</div>
</div>
</div>
</div>

<div class="section">
<h3>指定頻道自動踢人</h3>

<p class="note">
監控頻道與上方聊天室完全獨立。
一般成員在指定頻道發送訊息時，機器人會嘗試將其踢出。
管理員、伺服器擁有者及指定身分組不受影響。
</p>

<label>監控伺服器</label>
<select id="kickGuild"></select>

<label>監控頻道</label>
<select id="kickChannel"></select>

<p class="note" id="kickChannelStatus">正在載入監控頻道...</p>

<label>額外豁免身分組 ID</label>
<input id="exemptRoles" placeholder="多個 ID 請用逗號分隔，可留空">

<button onclick="saveKickRule()">儲存並啟用自動踢人</button>
<button class="gray" onclick="disableKickRule()">關閉自動踢人</button>

<p class="result" id="kickStatus">自動踢人目前關閉</p>
</div>

<button class="gray" onclick="location.href='/logout'">登出</button>

<script>
const $ = id => document.getElementById(id);

let currentChannel = null;
let lastMessageId = null;
let shown = new Set();
let polling = false;

async function api(url, options={}) {
    const response = await fetch(url, options);
    const data = await response.json();

    if (!response.ok) {
        throw new Error(data.error || "請求失敗");
    }

    return data;
}

function addOption(select, label, value) {
    select.add(new Option(label, value));
}

function timeText(value) {
    return new Date(value).toLocaleString("zh-TW");
}

function appendMessage(m, scroll=true) {
    if (shown.has(String(m.id))) return;

    shown.add(String(m.id));
    lastMessageId = String(m.id);

    const row = document.createElement("div");
    row.className = "msg";

    const avatar = document.createElement("img");
    avatar.className = "avatar";
    avatar.src = m.avatar || "";
    avatar.onerror = () => avatar.style.visibility = "hidden";

    const body = document.createElement("div");
    body.className = "msg-body";

    const head = document.createElement("div");
    head.className = "msg-head";

    const name = document.createElement("span");
    name.className = "msg-name";
    name.textContent = m.display_name || m.author;

    const time = document.createElement("span");
    time.className = "msg-time";
    time.textContent = timeText(m.created_at);

    head.append(name,time);

    const content = document.createElement("div");
    content.className = "msg-content";
    content.textContent = m.content || "";

    body.append(head,content);

    (m.attachments || []).forEach(a => {
        if ((a.content_type || "").startsWith("image/")) {
            const img = document.createElement("img");
            img.className = "msg-image";
            img.src = a.url;
            body.append(img);
        } else {
            const link = document.createElement("a");
            link.href = a.url;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            link.textContent = a.filename;
            body.append(link);
        }
    });

    row.append(avatar,body);
    $("chatLog").append(row);

    if (scroll) {
        $("chatLog").scrollTop = $("chatLog").scrollHeight;
    }
}

async function getChannels(guildId) {
    return await api("/api/channels/" + guildId);
}

async function loadGuilds() {
    const guilds = await api("/api/guilds");

    ["chatGuild","kickGuild"].forEach(id => {
        const select = $(id);
        select.innerHTML = "";

        guilds.forEach(g => {
            addOption(select,g.name,g.id);
        });
    });

    if (!guilds.length) {
        throw new Error("機器人尚未加入伺服器");
    }
}

async function loadChatChannels() {
    const guildId = $("chatGuild").value;
    const select = $("chatChannel");

    select.innerHTML = "";
    currentChannel = null;
    lastMessageId = null;
    shown.clear();
    $("chatLog").textContent = "";

    if (!guildId) return;

    try {
        const channels = await getChannels(guildId);

        channels.filter(c => c.can_read).forEach(c => {
            addOption(select,"# " + c.name,c.id);
        });

        if (select.options.length) {
            await loadMessages();
        } else {
            $("chatStatus").textContent = "沒有可讀取的頻道";
        }
    } catch(e) {
        $("chatStatus").textContent = e.message;
    }
}

async function loadMessages() {
    const channelId = $("chatChannel").value;
    if (!channelId) return;

    currentChannel = channelId;
    lastMessageId = null;
    shown.clear();

    $("chatLog").textContent = "正在載入...";
    $("chatTitle").textContent =
        $("chatChannel").selectedOptions[0].text;

    try {
        const data = await api("/api/messages/" + channelId);

        if (currentChannel !== channelId) return;

        $("chatLog").innerHTML = "";
        data.messages.forEach(m => appendMessage(m,false));
        $("chatLog").scrollTop = $("chatLog").scrollHeight;
        $("chatStatus").textContent = "已載入 " + data.messages.length + " 則訊息";
    } catch(e) {
        $("chatLog").textContent = e.message;
    }
}

async function pollMessages() {
    if (polling || !currentChannel || !lastMessageId) return;

    polling = true;
    const channel = currentChannel;

    try {
        const data = await api(
            "/api/messages/" + channel + "?after=" + lastMessageId
        );

        if (channel === currentChannel) {
            data.messages.forEach(m => appendMessage(m));
        }
    } catch(e) {
        console.log(e);
    } finally {
        polling = false;
    }
}

async function sendMessage() {
    try {
        const channel = $("chatChannel").value;
        const message = $("message").value.trim();

        if (!channel || !message) {
            throw new Error("請選擇頻道並輸入訊息");
        }

        const data = await api("/api/send",{
            method:"POST",
            headers:{"Content-Type":"application/json"},
            body:JSON.stringify({channel,message})
        });

        $("sendStatus").textContent = data.message;
        $("message").value = "";

        if (data.sent_message) appendMessage(data.sent_message);
    } catch(e) {
        $("sendStatus").textContent = e.message;
    }
}

async function sendImage() {
    try {
        const channel = $("chatChannel").value;
        const file = $("image").files[0];

        if (!channel || !file) throw new Error("請選擇頻道及圖片");

        const form = new FormData();
        form.append("image",file);

        const data = await api("/api/image/" + channel,{
            method:"POST",
            body:form
        });

        $("sendStatus").textContent = data.message;
        if (data.sent_message) appendMessage(data.sent_message);
    } catch(e) {
        $("sendStatus").textContent = e.message;
    }

    $("image").value = "";
}

async function loadKickChannels() {
    const guildId = $("kickGuild").value;
    const select = $("kickChannel");

    select.innerHTML = "";
    $("kickChannelStatus").textContent = "正在載入監控頻道...";

    if (!guildId) {
        $("kickChannelStatus").textContent = "請先選擇監控伺服器";
        return;
    }

    try {
        const channels = await getChannels(guildId);

        select.innerHTML = "";

        channels.forEach(c => {
            addOption(select,"# " + c.name,c.id);
        });

        if (channels.length) {
            $("kickChannelStatus").textContent =
                "成功載入 " + channels.length + " 個可查看的文字頻道";
        } else {
            $("kickChannelStatus").textContent =
                "沒有可查看的文字頻道";
        }

    } catch(e) {
        $("kickChannelStatus").textContent = "載入失敗：" + e.message;
    }
}

async function loadKickConfig() {
    const config = await api("/api/auto-kick");

    // 核心修正：
    // 沒有舊設定時，使用第一個伺服器；
    // 不論有沒有舊設定，都一定載入監控頻道。

    if (config.guild_id) {
        $("kickGuild").value = String(config.guild_id);
    }

    if (!$("kickGuild").value && $("kickGuild").options.length) {
        $("kickGuild").selectedIndex = 0;
    }

    await loadKickChannels();

    if (config.channel_id) {
        $("kickChannel").value = String(config.channel_id);
    }

    $("exemptRoles").value =
        (config.exempt_role_ids || []).join(",");

    $("kickStatus").textContent = config.enabled
        ? "目前已啟用監控"
        : "自動踢人目前關閉";
}

async function saveKickRule() {
    try {
        const guild = $("kickGuild").value;
        const channel = $("kickChannel").value;

        if (!guild || !channel) {
            throw new Error("請選擇監控伺服器及頻道");
        }

        const data = await api("/api/auto-kick",{
            method:"POST",
            headers:{"Content-Type":"application/json"},
            body:JSON.stringify({
                enabled:true,
                guild,
                channel,
                roles:$("exemptRoles").value
            })
        });

        $("kickStatus").textContent = data.message;
    } catch(e) {
        $("kickStatus").textContent = e.message;
    }
}

async function disableKickRule() {
    try {
        const data = await api("/api/auto-kick",{
            method:"POST",
            headers:{"Content-Type":"application/json"},
            body:JSON.stringify({enabled:false})
        });

        $("kickStatus").textContent = data.message;
    } catch(e) {
        $("kickStatus").textContent = e.message;
    }
}

$("message").addEventListener("keydown",e => {
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

async function init() {
    try {
        const status = await api("/api/status");

        $("botStatus").textContent = status.online
            ? "機器人已上線：" + status.name
            : "機器人尚未上線";

        if (!status.online) return;

        await loadGuilds();

        $("chatGuild").onchange = loadChatChannels;
        $("chatChannel").onchange = loadMessages;
        $("kickGuild").onchange = loadKickChannels;

        await loadChatChannels();

        // 無論是否有舊設定，都會載入監控頻道
        await loadKickConfig();

    } catch(e) {
        $("botStatus").textContent = "載入錯誤：" + e.message;
        console.error(e);
    }
}

init();
setInterval(pollMessages,3000);

</script>
{% endif %}
</div>
</body>
</html>
"""


# =========================
# 登入及首頁
# =========================

@app.route("/")
def home():
    return render_template_string(
        HTML,
        logged_in=current_user.is_authenticated
    )


@app.route("/login", methods=["POST"])
def login():
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return "禁止登入", 403

    if request.form.get("password") != ADMIN_PASSWORD:
        return "密碼錯誤", 401

    login_user(Admin())
    return redirect("/")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect("/")


# =========================
# 狀態
# =========================

@app.route("/api/status")
@login_required
def status():
    return jsonify(
        online=bot.is_ready(),
        name=str(bot.user) if bot.user else ""
    )


# =========================
# 伺服器
# =========================

@app.route("/api/guilds")
@login_required
def guilds():
    if not bot.is_ready():
        return jsonify(error="機器人尚未上線"), 503

    return jsonify([
        {"id": str(g.id), "name": g.name}
        for g in bot.guilds
    ])


# =========================
# 頻道
# =========================

@app.route("/api/channels/<int:guild_id>")
@login_required
def channels(guild_id):
    if not bot.is_ready():
        return jsonify(error="機器人尚未上線"), 503

    guild = bot.get_guild(guild_id)

    if guild is None:
        return jsonify(error="找不到伺服器"), 404

    if guild.me is None:
        return jsonify(error="Bot 尚未準備完成"), 503

    result = []

    for channel in guild.text_channels:
        permissions = channel.permissions_for(guild.me)

        if permissions.view_channel:
            result.append({
                "id": str(channel.id),
                "name": channel.name,
                "can_read": permissions.read_message_history,
                "can_send": permissions.send_messages
            })

    return jsonify(result)


# =========================
# 訊息歷史
# =========================

@app.route("/api/messages/<int:channel_id>")
@login_required
def messages(channel_id):
    channel = get_text_channel(channel_id)

    if not channel:
        return jsonify(error="找不到文字頻道"), 404

    permissions = channel.permissions_for(channel.guild.me)

    if not permissions.view_channel:
        return jsonify(error="沒有查看頻道權限"), 403

    if not permissions.read_message_history:
        return jsonify(error="沒有讀取訊息歷史權限"), 403

    after = request.args.get("after")

    if after and not after.isdigit():
        return jsonify(error="訊息 ID 錯誤"), 400

    try:
        result = run_async(read_messages(channel,after),35)
        return jsonify(messages=result)
    except Exception as e:
        print("[訊息讀取錯誤]",e)
        return jsonify(error="讀取訊息失敗"),500


# =========================
# 發送文字
# =========================

@app.route("/api/send",methods=["POST"])
@login_required
def send():
    data = request.get_json(silent=True) or {}

    channel = get_text_channel(data.get("channel"))
    message = str(data.get("message","")).strip()

    if not channel:
        return jsonify(error="找不到頻道"),400

    if not message:
        return jsonify(error="訊息不可空白"),400

    if len(message)>2000:
        return jsonify(error="訊息不可超過 2000 字"),400

    if not channel.permissions_for(channel.guild.me).send_messages:
        return jsonify(error="Bot 沒有發送訊息權限"),403

    try:
        sent = run_async(channel.send(message))
        return jsonify(
            message="訊息發送成功",
            sent_message=message_data(sent)
        )
    except Exception as e:
        print("[發送錯誤]",e)
        return jsonify(error="發送失敗"),500


# =========================
# 發送圖片
# =========================

@app.route("/api/image/<int:channel_id>",methods=["POST"])
@login_required
def image_upload(channel_id):
    channel = get_text_channel(channel_id)
    file = request.files.get("image")

    if not channel or not file:
        return jsonify(error="請選擇頻道及圖片"),400

    if not file.mimetype or not file.mimetype.startswith("image/"):
        return jsonify(error="只允許圖片"),400

    content = file.read(8*1024*1024+1)

    if len(content)>8*1024*1024:
        return jsonify(error="圖片不可超過 8MB"),400

    filename = secure_filename(file.filename or "image.png") or "image.png"

    try:
        sent = run_async(channel.send(
            file=discord.File(io.BytesIO(content),filename=filename)
        ))

        return jsonify(
            message="圖片發送成功",
            sent_message=message_data(sent)
        )
    except Exception as e:
        print("[圖片發送錯誤]",e)
        return jsonify(error="圖片發送失敗"),500


# =========================
# 自動踢人設定
# =========================

@app.route("/api/auto-kick",methods=["GET","POST"])
@login_required
def auto_kick():
    if request.method=="GET":
        return jsonify(
            enabled=moderation_config["enabled"],
            guild_id=moderation_config["guild_id"],
            channel_id=moderation_config["channel_id"],
            exempt_role_ids=moderation_config["exempt_role_ids"]
        )

    data = request.get_json(silent=True) or {}

    if data.get("enabled") is not True:
        moderation_config["enabled"] = False
        save_config()
        return jsonify(message="自動踢人已關閉")

    try:
        guild_id = int(data.get("guild"))
        channel_id = int(data.get("channel"))
    except (TypeError,ValueError):
        return jsonify(error="伺服器或頻道 ID 不正確"),400

    guild = bot.get_guild(guild_id)
    channel = bot.get_channel(channel_id)

    if not guild or not isinstance(channel,discord.TextChannel):
        return jsonify(error="找不到伺服器或頻道"),400

    if channel.guild.id != guild.id:
        return jsonify(error="頻道不屬於該伺服器"),400

    if guild.me is None or not guild.me.guild_permissions.kick_members:
        return jsonify(error="Bot 缺少踢出成員權限"),403

    if not channel.permissions_for(guild.me).send_messages:
        return jsonify(error="Bot 在監控頻道沒有發送通知權限"),403

    roles = []

    for value in str(data.get("roles","")).split(","):
        value = value.strip()

        if not value:
            continue

        if not value.isdigit():
            return jsonify(error="身分組 ID 必須是數字"),400

        roles.append(int(value))

    moderation_config.update({
        "enabled":True,
        "guild_id":guild_id,
        "channel_id":channel_id,
        "exempt_role_ids":roles
    })

    save_config()

    return jsonify(
        message="已儲存並啟用監控：" + guild.name + " / #" + channel.name
    )


# =========================
# Discord 事件
# =========================

@bot.event
async def on_ready():
    print("="*40)
    print("機器人已上線：",bot.user)
    print("伺服器數量：",len(bot.guilds))
    print("自動踢人：",moderation_config["enabled"])
    print("="*40)


@bot.event
async def on_message(message):
    if not message.guild or message.author.bot:
        return

    if not moderation_config["enabled"]:
        return

    if message.guild.id != moderation_config["guild_id"]:
        return

    if message.channel.id != moderation_config["channel_id"]:
        return

    member = message.author
    guild = message.guild

    if member.id == guild.owner_id:
        return

    if member.guild_permissions.administrator:
        return

    exempt_roles = set(moderation_config["exempt_role_ids"])

    if any(role.id in exempt_roles for role in member.roles):
        return

    if guild.me is None or not guild.me.guild_permissions.kick_members:
        print("[踢人失敗] Bot 沒有踢人權限")
        return

    if member.top_role >= guild.me.top_role:
        print("[踢人失敗] 對方身分組高於或等於 Bot")
        return

    try:
        await member.kick(
            reason="在指定監控頻道發送訊息"
        )

        print("[自動踢人] 已踢出：",member)

        try:
            await message.channel.send(
                "已將 **" +
                discord.utils.escape_markdown(str(member)) +
                "** 踢出伺服器。"
            )
        except discord.HTTPException as e:
            print("[通知失敗]",e)

    except discord.Forbidden:
        print("[自動踢人] 權限不足")

    except discord.HTTPException as e:
        print("[自動踢人錯誤]",e)


# =========================
# 啟動
# =========================


def run_web():
    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
        threaded=True
    )


if __name__ == "__main__":
    threading.Thread(
        target=run_web,
        daemon=True
    ).start()

    bot.run(TOKEN)