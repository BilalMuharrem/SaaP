import sys
import os
import uuid
from app import app
from models import db, User, Plan, TrackedProduct

def test_quota_v3():
    with app.app_context():
        # 1. Setup Temporary User
        test_email = f"test_{uuid.uuid4().hex[:6]}@example.com"
        plan = Plan.query.filter_by(name='professional').first() # Limit is 30
        if not plan:
            plan = Plan.query.filter_by(name='starter').first() # Limit is 10
            
        user = User(
            email=test_email,
            full_name="Quota Test User",
            plan_id=plan.id,
            is_active=True,
            is_approved=True
        )
        user.set_password("testpass")
        db.session.add(user)
        db.session.commit()
        
        print(f"Testing for new user: {user.email} (Plan: {plan.name}, Max: {plan.max_tracked_products})")
        
        try:
            # 2. Initial Quota
            initial_quota = user.remaining_tracked_quota
            print(f"Initial remaining_tracked_quota: {initial_quota}")
            
            # 3. Add a product
            new_tp = TrackedProduct(
                user_id=user.id,
                url="https://test.com/product-new",
                is_active=True
            )
            db.session.add(new_tp)
            db.session.commit()
            
            # 4. Check Updated Quota
            updated_quota = user.remaining_tracked_quota
            print(f"Updated remaining_tracked_quota: {updated_quota}")
            
            if updated_quota == initial_quota - 1:
                print("Quota correctly updated automatically!")
                success = True
            else:
                print(f"Quota update FAILED! Expected {initial_quota-1}, got {updated_quota}")
                success = False
        finally:
            # 5. Cleanup
            TrackedProduct.query.filter_by(user_id=user.id).delete()
            db.session.delete(user)
            db.session.commit()
            
        return success

if __name__ == "__main__":
    if test_quota_v3():
        print("\nQUOTA TEST PASSED!")
        sys.exit(0)
    else:
        print("\nQUOTA TEST FAILED!")
        sys.exit(1)
