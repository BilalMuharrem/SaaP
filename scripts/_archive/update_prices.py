from app import app, db
from models import Plan

with app.app_context():
    p1 = Plan.query.filter_by(name='starter').first()
    if p1: p1.price_monthly = 499
    
    p2 = Plan.query.filter_by(name='professional').first()
    if p2: p2.price_monthly = 1499
    
    p3 = Plan.query.filter_by(name='enterprise').first()
    if p3: p3.price_monthly = 4999
    
    db.session.commit()
    print("Successfully updated database prices to: Starter(499), Pro(1499), Enterprise(4999).")
