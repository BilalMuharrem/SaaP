from app import app
from models import db, Notification, User
from datetime import datetime

with app.app_context():
    # Grab the primary active user assuming ID=1 or first
    user = User.query.first()
    if user:
        # Check if dummy data exists
        existing = Notification.query.filter(Notification.message.like('%Fırsat: Tefal Tost Expert%')).first()
        if not existing:
            notif = Notification(
                user_id=user.id,
                message="🌟 Fırsat: Tefal Tost Expert ürünündeki rakibinizin satıcı puanı 8.2'ye düştü. Buy Box algoritması sizi öne çıkarıyor, fiyatınızı %3 artırmak için uygun bir zaman.",
                link="/tracked-products",
                is_read=False,
                created_at=datetime.utcnow()
            )
            db.session.add(notif)
            db.session.commit()
            print("Successfully seeded opportunity notification.")
        else:
            print("Opportunity notification already exists.")
    else:
        print("No users found to seed.")
