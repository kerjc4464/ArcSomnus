"""发信：冻存自 ArcViGil-BackEnd/scheduler.py（2026-09-09 拷入）。
只取 SMTP + Resend 双通道 + 附件逻辑，云端定时（scheduled）不要。
以后 ViGil 修 bug 这里享受不到，各改各的，代价已知。"""
import base64
import mimetypes
import os
import smtplib
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

FROM_NAME = "ArcSomnus"


def _build_attachments(attachments):
    """将附件路径列表转换为 [(路径, 文件名, MIME 子类型), ...]，跳过不存在的文件"""
    result = []
    for path in attachments or []:
        if not os.path.isfile(path):
            continue
        fname = os.path.basename(path)
        mime_type, _ = mimetypes.guess_type(fname)
        subtype = mime_type.split("/")[-1] if mime_type else "octet-stream"
        result.append((path, fname, subtype))
    return result


def send_smtp_email(content, topic, participants, config, attachments=None):
    server = config.get("smtp_server", "")
    try:
        port = int(config.get("smtp_port", 465))
    except Exception:
        port = 465
    user = config.get("smtp_user", "")
    password = config.get("smtp_pass", "")
    target = config.get("target_email", "")
    if not server or not user or not target:
        return False
    sender_name = " & ".join(participants)
    msg = MIMEMultipart()
    msg["From"] = "%s <%s>" % (sender_name, user)
    msg["To"] = target
    msg["Subject"] = "来自 %s 的一封信：%s" % (sender_name, topic)
    msg.attach(MIMEText(content, "plain", "utf-8"))
    for path, fname, subtype in _build_attachments(attachments):
        try:
            with open(path, "rb") as f:
                img = MIMEImage(f.read(), _subtype=subtype)
            img.add_header("Content-Disposition", "attachment", filename=fname)
            msg.attach(img)
        except Exception:
            pass
    try:
        if port == 465:
            with smtplib.SMTP_SSL(server, port, timeout=30) as smtp:
                smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(server, port, timeout=30) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                smtp.login(user, password)
                smtp.send_message(msg)
        return True
    except Exception:
        return False


def send_resend_email(content, topic, participants, config, attachments=None):
    api_key = config.get("resend_key", "")
    target = config.get("target_email", "")
    from_addr = config.get("resend_from", "%s <onboarding@resend.dev>" % FROM_NAME)
    if not api_key or not target:
        return False
    sender_name = " & ".join(participants)
    payload = {"from": from_addr, "to": [target],
               "subject": "来自 %s 的一封信：%s" % (sender_name, topic),
               "text": content}
    built = _build_attachments(attachments)
    if built:
        payload["attachments"] = []
        for path, fname, _sub in built:
            try:
                with open(path, "rb") as f:
                    payload["attachments"].append({
                        "filename": fname,
                        "content": base64.b64encode(f.read()).decode("utf-8")})
            except Exception:
                pass
    try:
        res = requests.post("https://api.resend.com/emails",
                            headers={"Authorization": "Bearer " + api_key,
                                     "Content-Type": "application/json"},
                            json=payload, timeout=30)
        return res.status_code in (200, 201)
    except Exception:
        return False


def send_email(content, topic, participants, config, attachments=None):
    """统一发信入口：email_method = smtp / resend"""
    if config.get("email_method", "smtp") == "resend":
        return send_resend_email(content, topic, participants, config, attachments)
    return send_smtp_email(content, topic, participants, config, attachments)


def flush_pending(config):
    """把 letters 里 pending 的真发出去。返回 (sent, failed)。成功标 sent，失败保留 pending 下次再试。"""
    import store
    conn = store.db()
    try:
        rows = conn.execute("SELECT id, soul_id, subject, content FROM letters"
                            " WHERE status='pending' ORDER BY id LIMIT 10").fetchall()
    finally:
        conn.close()
    sent, failed = 0, 0
    for lid, soul, subject, content in rows:
        ok = send_email(content, subject, [soul], config)
        conn = store.db()
        try:
            conn.execute("UPDATE letters SET status=? WHERE id=?",
                         ("sent" if ok else "pending", lid))
            conn.commit()
        finally:
            conn.close()
        sent, failed = sent + (1 if ok else 0), failed + (0 if ok else 1)
    return sent, failed
