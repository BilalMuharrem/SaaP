import json
import logging
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
    _TR_TZ = ZoneInfo("Europe/Istanbul")
except Exception:  # pragma: no cover — eski Python fallback
    _TR_TZ = None

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from extensions import db

log = logging.getLogger(__name__)


def get_tr_now():
    """Türkiye saati (Europe/Istanbul). DÖNÜŞ: naive datetime — eski uyumlu.

    Faz 10B düzeltmesi: Eskiden `datetime.utcnow() + timedelta(hours=3)` idi —
    DST geri gelirse (Türkiye değiştirebilir) yanlış saat verirdi. ZoneInfo
    kullanımı doğru/sürdürülebilir.

    DB kolonlarımız hâlâ naive `DateTime` olduğu için tzinfo'yu çıkarıp döndürürüz
    (aware vs naive karşılaştırması TypeError verir). Tam TZ-aware geçiş
    ileride yapılacak (db migration + worker.py'de tüm karşılaştırmalar).
    """
    if _TR_TZ is not None:
        return datetime.now(_TR_TZ).replace(tzinfo=None)
    # Fallback: ZoneInfo yoksa eski davranış
    return datetime.utcnow() + timedelta(hours=3)


class Plan(db.Model):
    __tablename__ = 'plans'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False, unique=True)
    display_name = db.Column(db.String(100), nullable=False)
    max_requests = db.Column(db.Integer, default=0)  # 0 limit means unlimited
    max_tracked_products = db.Column(db.Integer, default=5) # 0 means unlimited
    period_type = db.Column(db.String(20), default='monthly') # daily, weekly, monthly
    features = db.Column(db.Text, nullable=True) # JSON list of featuresice', 'review', 'combined']
    price_monthly = db.Column(db.Float, default=0.0)
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)

    users = db.relationship('User', backref='plan', lazy=True)

    def get_features(self):
        try:
            return json.loads(self.features)
        except (json.JSONDecodeError, TypeError):
            return ['price', 'review', 'combined']

    def __repr__(self):
        return f'<Plan {self.name}>'


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    full_name = db.Column(db.String(100), nullable=False)
    company = db.Column(db.String(100), default='')
    phone = db.Column(db.String(20), default='')
    is_admin = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=False)  # Must be approved
    is_approved = db.Column(db.Boolean, default=False)
    plan_id = db.Column(db.Integer, db.ForeignKey('plans.id'), nullable=True)
    trial_start = db.Column(db.DateTime, nullable=True)
    trial_days = db.Column(db.Integer, default=14)
    created_at = db.Column(db.DateTime, default=get_tr_now)
    last_login = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.Text, default='')
    # FAZ 5A: Onboarding wizard tamamlandı mı? Kayıt sonrası ilk girişte /onboarding'e
    # yönlendiriyoruz; "Atla" veya 3 adımı tamamlama ile True olur.
    onboarding_completed = db.Column(db.Boolean, default=False, nullable=False)

    jobs = db.relationship('Job', backref='user', lazy=True, order_by='Job.created_at.desc()')
    usage_logs = db.relationship('UsageLog', backref='user', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_trial_active(self):
        # If user has been assigned a real plan (not trial), trial is irrelevant
        if self.plan and self.plan.name != 'trial':
            return False
        if not self.trial_start:
            return False
        trial_end = self.trial_start + timedelta(days=self.trial_days)
        return get_tr_now() <= trial_end

    @property
    def trial_days_left(self):
        if not self.trial_start:
            return 0
        trial_end = self.trial_start + timedelta(days=self.trial_days)
        delta = trial_end - get_tr_now()
        return max(0, delta.days)

    @property
    def can_submit(self):
        """Check if the user can submit a new request based on their plan quota."""
        if self.is_admin:
            return True
        if not self.is_active or not self.is_approved:
            return False
        if not self.plan:
            return self.is_trial_active
        return self.remaining_quota > 0

    @property
    def has_premium_access(self):
        """Check if user has access to premium features (Professional or Enterprise)."""
        if self.is_admin:
            return True
        if not self.plan:
            return False
        return self.plan.name in ['professional', 'enterprise']

    @property
    def has_enterprise_access(self):
        """Check if user has access to enterprise features (AI Consultant)."""
        if self.is_admin:
            return True
        if not self.plan:
            return False
        return self.plan.name == 'enterprise'

    @property
    def remaining_quota(self):
        """Calculate remaining requests in current period."""
        if self.is_admin:
            return 999
        plan = self.plan
        if not plan:
            return 1 if self.is_trial_active else 0

        if plan.max_requests <= 0:  # Unlimited
            return 999

        now = get_tr_now()
        if plan.period_type == 'daily':
            period_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif plan.period_type == 'weekly':
            period_start = now - timedelta(days=now.weekday())
            period_start = period_start.replace(hour=0, minute=0, second=0, microsecond=0)
        else:  # monthly
            period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        used = Job.query.filter(
            Job.user_id == self.id,
            Job.created_at >= period_start,
            Job.status != 'failed'
        ).count()

        return max(0, plan.max_requests - used)
    
    @property
    def remaining_tracked_quota(self):
        """Calculate remaining active tracked product slots."""
        if self.is_admin:
            return 999
        plan = self.plan
        if not plan:
            return 5 if self.is_trial_active else 0
            
        if plan.max_tracked_products <= 0: # Unlimited
            return 999
            
        current_count = TrackedProduct.query.filter_by(
            user_id=self.id,
            is_active=True
        ).count()
        
        return max(0, plan.max_tracked_products - current_count)

    @property
    def period_label(self):
        if not self.plan:
            return 'deneme'
        labels = {'daily': 'günlük', 'weekly': 'haftalık', 'monthly': 'aylık'}
        return labels.get(self.plan.period_type, self.plan.period_type)

    def __repr__(self):
        return f'<User {self.email}>'


class Job(db.Model):
    __tablename__ = 'jobs'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    job_type = db.Column(db.String(20), nullable=False)  # price, review, combined
    urls = db.Column(db.Text, nullable=False)  # JSON array
    status = db.Column(db.String(20), default='pending', index=True)  # pending, running, completed, failed
    result_html = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=get_tr_now, index=True)
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    api_key_used = db.Column(db.String(100), default='')

    def get_urls(self, filter_metadata=True):
        try:
            raw_urls = json.loads(self.urls)
            if filter_metadata:
                # Filter out metadata markers like __COST__: from the UI-facing URL list
                return [u for u in raw_urls if not str(u).startswith('__COST__:')]
            return raw_urls
        except (json.JSONDecodeError, TypeError):
            return []

    def set_urls(self, url_list):
        self.urls = json.dumps(url_list)

    @property
    def duration_str(self):
        if self.started_at and self.completed_at:
            delta = self.completed_at - self.started_at
            minutes = int(delta.total_seconds() // 60)
            seconds = int(delta.total_seconds() % 60)
            if minutes > 0:
                return f'{minutes}dk {seconds}sn'
            return f'{seconds}sn'
        return '-'

    @property
    def type_label(self):
        labels = {'price': '💰 Fiyat Analizi', 'review': '🗣️ Yorum Analizi', 'combined': '🔄 Kombine Analiz'}
        return labels.get(self.job_type, self.job_type)

    @property
    def status_label(self):
        labels = {
            'pending': '⏳ Bekliyor',
            'running': '🔄 Çalışıyor',
            'completed': '✅ Tamamlandı',
            'failed': '❌ Başarısız'
        }
        return labels.get(self.status, self.status)

    def __repr__(self):
        return f'<Job {self.id} {self.job_type} {self.status}>'


class UsageLog(db.Model):
    __tablename__ = 'usage_logs'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    job_id = db.Column(db.Integer, db.ForeignKey('jobs.id'), nullable=True)
    action = db.Column(db.String(50), nullable=False)  # login, submit_job, view_result
    details = db.Column(db.Text, default='')
    timestamp = db.Column(db.DateTime, default=get_tr_now, index=True)

    def __repr__(self):
        return f'<UsageLog {self.action} by user {self.user_id}>'


class Setting(db.Model):
    __tablename__ = 'settings'
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)
    value = db.Column(db.Text, default='')

    @staticmethod
    def get(key, default=''):
        s = Setting.query.filter_by(key=key).first()
        return s.value if s else default

    @staticmethod
    def set(key, value):
        s = Setting.query.filter_by(key=key).first()
        if s:
            s.value = str(value)
        else:
            s = Setting(key=key, value=str(value))
            db.session.add(s)
        db.session.commit()


class TrackedProduct(db.Model):
    __tablename__ = 'tracked_products'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    url = db.Column(db.String(500), nullable=False)
    platform_name = db.Column(db.String(50), nullable=True)
    product_name = db.Column(db.String(255), nullable=True)
    current_price = db.Column(db.Float, default=0.0)
    previous_price = db.Column(db.Float, default=0.0)
    current_stock = db.Column(db.Integer, default=-1)  # -1 = bilinmiyor
    target_price = db.Column(db.Float, nullable=True)
    # FAZ 1: Net Kâr ve Dinamik ROI Simülatörü — kullanıcının bildirdiği birim tedarik maliyeti
    # Bu alan SADECE base ürüne (is_base_product=True) yazılır. Grubun tamamı base'in
    # unit_cost değerini kullanır. NULL ise grafikte "Maliyet Çizgisi" gösterilmez.
    unit_cost = db.Column(db.Float, nullable=True)
    # FAZ 4: Yorum ve Kalite İstihbaratı — scraper, fiyatla birlikte yıldız puanını ve
    # yorum sayısını da çeker. NULL/0 olabilir; YZ Danışman boş ise kalite analizini atlar.
    rating = db.Column(db.Float, nullable=True)         # 0.0 — 5.0
    review_count = db.Column(db.Integer, nullable=True, default=0)
    # HOTFIX 1.35: Satıcı (mağaza) puanı — Trendyol seller-store header API'sinden
    # çekiliyor. 0.0 — 10.0 ölçeği (Trendyol bu skala). NULL = henüz çekilmedi /
    # platformda satıcı puanı kavramı yok.
    seller_name = db.Column(db.String(100), nullable=True)
    seller_rating = db.Column(db.Float, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    group_id = db.Column(db.String(50), nullable=True)
    tracking_type = db.Column(db.String(20), default='price')  # DEPRECATED: kept for backward compat
    # ── Multi-Tracking Flags ──
    # Bir ürün aynı anda HEM Fiyat Takibinde HEM de Zafiyet Radarında olabilir.
    is_price_tracked = db.Column(db.Boolean, default=True)
    is_radar_tracked = db.Column(db.Boolean, default=False)
    is_base_product = db.Column(db.Boolean, default=False)
    # FAZ 7C: Sistem tarafından kayıt sırasında otomatik eklenen örnek ürün mü?
    # True ise UI'da "ÖRNEK" badge'i gösterilir, dashboard banner farklı renderlanır.
    is_demo = db.Column(db.Boolean, default=False, nullable=False)
    last_checked = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=get_tr_now)
    # HOTFIX 1.87: Kullanıcının grup için verdiği özel ad ("Yaz Kampanyası" gibi).
    # Sadece BASE üründe (is_base_product=True) doldurulur; UI tüm grup için bunu
    # kullanır. NULL ise eski davranış (product_name'in ilk 40 karakteri) fallback.
    group_label = db.Column(db.String(100), nullable=True)
    # HOTFIX 1.91: Global ürün havuzu FK — aynı URL'i takip eden tüm kullanıcılar
    # tek bir GlobalProduct'a bağlanır. NULL = eski kayıt (backfill ile doldurulur).
    global_product_id = db.Column(db.Integer, db.ForeignKey('global_products.id'),
                                  nullable=True, index=True)
    
    user = db.relationship('User', backref=db.backref('tracked_products', lazy=True, order_by='TrackedProduct.created_at.desc()'))


class PriceHistory(db.Model):
    __tablename__ = 'price_history'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('tracked_products.id'), nullable=False)
    price = db.Column(db.Float, nullable=False)
    timestamp = db.Column(db.DateTime, default=get_tr_now, index=True)
    
    product = db.relationship('TrackedProduct', backref=db.backref('history', lazy=True, cascade='all, delete-orphan', order_by='PriceHistory.timestamp.asc()'))


class StockHistory(db.Model):
    __tablename__ = 'stock_history'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('tracked_products.id'), nullable=False)
    stock = db.Column(db.Integer, nullable=False)
    timestamp = db.Column(db.DateTime, default=get_tr_now, index=True)

    product = db.relationship('TrackedProduct', backref=db.backref('stock_history', lazy=True, cascade='all, delete-orphan', order_by='StockHistory.timestamp.asc()'))


class VulnerabilityAlert(db.Model):
    __tablename__ = 'vulnerability_alerts'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('tracked_products.id'), nullable=False)
    alert_type = db.Column(db.String(20), nullable=False)  # 'opportunity' or 'threat'
    message = db.Column(db.Text, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=get_tr_now, index=True)

    user = db.relationship('User', backref=db.backref('vulnerability_alerts', lazy=True, order_by='VulnerabilityAlert.created_at.desc()'))
    product = db.relationship('TrackedProduct', backref=db.backref('vulnerability_alerts', lazy=True, cascade='all, delete-orphan'))


class Notification(db.Model):
    __tablename__ = 'notifications'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    message = db.Column(db.Text, nullable=False)
    link = db.Column(db.String(500), nullable=True)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=get_tr_now, index=True)
    # ── HOTFIX 1.54: Bildirim sınıflandırması ──────────────────────────────
    # 8 sabit kategori (HOTFIX 1.99'da 'seo' eklendi):
    # price_down, price_up, combined, opportunity, threat, system, seo, NULL
    category = db.Column(db.String(30), nullable=True, index=True)
    # ── HOTFIX 1.99: İç ve dış link ayrımı ──────────────────────────────────
    # `link` (mevcut) = dış URL (Trendyol/HB pazaryeri ürün sayfası)
    # `internal_link` = uygulama içi rota ("/tracked-products#group-X" veya
    #                   "/seo-graph#group-X") — bildirim kartından kullanıcı
    # iç grafiğe DOĞRUDAN gidebilsin diye.
    internal_link = db.Column(db.String(500), nullable=True)

    user = db.relationship('User', backref=db.backref('notifications', lazy=True, order_by='Notification.created_at.desc()'))


# ── FAZ 2.1: Akıllı Tetikleyiciler (Smart Alerts) — Çift Yönlü ─────────────
# Kullanıcı iki yönlü alarm kurabilir:
#   • price_below  → fiyat X ₺ altına DÜŞERSE 🚨 (fırsat)
#   • price_above  → fiyat X ₺ üstüne ÇIKARSA 📈 (rakip zam)
# İki alan da OPSİYONEL ama ikisi aynı anda NULL olamaz (uygulama katmanında doğrulanır).
# Worker, yeni fiyatı kaydettikten sonra aktif alarmları tarar; eşiklerden biri
# sağlanırsa Notification üretir ve is_active=False yaparak alarmı susturur.
class PriceAlert(db.Model):
    __tablename__ = 'price_alerts'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    tracked_product_id = db.Column(db.Integer, db.ForeignKey('tracked_products.id'), nullable=False, index=True)
    # Çift yönlü eşikler — ikisi de nullable
    price_below = db.Column(db.Float, nullable=True)   # Bu değerin altına inerse tetikle
    price_above = db.Column(db.Float, nullable=True)   # Bu değerin üstüne çıkarsa tetikle
    is_active = db.Column(db.Boolean, default=True, index=True)
    created_at = db.Column(db.DateTime, default=get_tr_now, index=True)

    user = db.relationship('User', backref=db.backref('price_alerts', lazy=True, order_by='PriceAlert.created_at.desc()'))
    product = db.relationship('TrackedProduct', backref=db.backref('price_alerts', lazy=True, cascade='all, delete-orphan'))


# ── FAZ 4: SEO ve Arama Sırası Takibi (Keyword Tracker) ────────────────────
# Kullanıcı arama kelimesi + kendi ürün linkini girer; worker periyodik olarak
# Trendyol arama sonuçlarında bu URL'in bulunduğu sayfa ve sıra numarasını saptar.
# current_page = 0, current_rank = 0 → ilk 5 sayfada bulunamadı (out-of-range).
class KeywordTracker(db.Model):
    __tablename__ = 'keyword_trackers'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    platform = db.Column(db.String(30), nullable=False, default='Trendyol')
    keyword = db.Column(db.String(200), nullable=False)
    target_url = db.Column(db.String(500), nullable=False)
    current_page = db.Column(db.Integer, default=0)
    current_rank = db.Column(db.Integer, default=0)
    previous_page = db.Column(db.Integer, default=0)
    previous_rank = db.Column(db.Integer, default=0)
    last_checked = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=get_tr_now, index=True)
    # HOTFIX 1.84: Bir tracker, fiyat takip grubuna bağlanabilir (toplu SEO için)
    group_id = db.Column(db.String(50), nullable=True, index=True)
    # HOTFIX 1.91: KeywordPool FK — aynı (platform,keyword,URL) kombinasyonu
    # paylaşan tüm kullanıcılar tek bir KeywordPool'a bağlanır.
    pool_id = db.Column(db.Integer, db.ForeignKey('keyword_pools.id'),
                        nullable=True, index=True)

    user = db.relationship('User', backref=db.backref('keyword_trackers', lazy=True, order_by='KeywordTracker.created_at.desc()'))


# ── HOTFIX 1.84: Tarihsel SEO Sıralama Kayıtları ──────────────────────────
# KeywordTracker = "şu an" sırası. SEOHistory = "tarih boyunca" sıralama kayıtları.
# Worker her tarama yaptığında bu tabloya yeni satır ekler — fiyat geçmişi (PriceHistory)
# paralelizmi. Grafik için: keyword_tracker_id'ye göre timeseries.
# Sıra 0 ise "ilk 5 sayfada bulunamadı"; >0 ise 1-200 arası gerçek sıra (page*40 + rank).
class SEOHistory(db.Model):
    __tablename__ = 'seo_history'
    id = db.Column(db.Integer, primary_key=True)
    keyword_tracker_id = db.Column(db.Integer, db.ForeignKey('keyword_trackers.id'),
                                   nullable=False, index=True)
    page = db.Column(db.Integer, default=0)         # 1-5 sayfa numarası, 0=bulunamadı
    rank = db.Column(db.Integer, default=0)         # 1-40 sayfa içi sıra
    overall_rank = db.Column(db.Integer, default=0) # (page-1)*40 + rank, kıyaslamak için
    timestamp = db.Column(db.DateTime, default=get_tr_now, index=True)

    # ── HOTFIX 1.89: Cascade Delete ──
    # KeywordTracker silindiğinde SQLAlchemy default davranışı SEOHistory satırlarının
    # FK'sini NULL'a set etmeye çalışıyor → keyword_tracker_id NOT NULL ihlali → 500.
    # `cascade="all, delete-orphan"` + `passive_deletes=False` ile birlikte:
    #   • tracker.delete() → bağlı tüm SEOHistory kayıtları da silinir
    #   • orphan history (tracker'ı kaybeden) anında temizlenir
    # NOT: backref'te `cascade` veriyoruz çünkü silme ana taraf olan KeywordTracker'dan
    # tetikleniyor. SEOHistory.tracker → tek; KeywordTracker.history → koleksiyon.
    tracker = db.relationship(
        'KeywordTracker',
        backref=db.backref(
            'history',
            lazy=True,
            order_by='SEOHistory.timestamp.asc()',
            cascade='all, delete-orphan',
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════
# EPIC TASK 5.0 / HOTFIX 1.91: GLOBAL ÜRÜN HAVUZU (BIG DATA MİMARİSİ)
# ───────────────────────────────────────────────────────────────────────────
# Mantık:
#   • GlobalProduct  — URL'ye göre TEKİL ürün kaydı (tüm kullanıcılar paylaşır)
#   • KeywordPool    — (platform, keyword, target_url) TEKİL SEO arama kaydı
#   • active_users_count → kaç kullanıcının panosunda olduğu
#   • is_dormant      → kimsenin takip etmediği "uyku" durumu (worker atlar)
#
# Migrasyon stratejisi:
#   • Mevcut TrackedProduct/KeywordTracker kayıtları korunur (KULLANICI BAZLI)
#   • Her TrackedProduct → bir GlobalProduct'a bağlanır (URL ile eşleşme)
#   • Worker hâlâ TrackedProduct döngüsü kullanır AMA önce GP.is_dormant kontrol eder
#   • Soft delete: TP silinince GP.active_users_count-- → 0 ise GP.is_dormant=True
#   • Aynı URL yeniden eklendi → GP.is_dormant=False + active_users_count++
# ═══════════════════════════════════════════════════════════════════════════

class GlobalProduct(db.Model):
    """URL bazlı tekil ürün kaydı. Tüm kullanıcılar tek bir GP'ye bağlanır."""
    __tablename__ = 'global_products'
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(700), nullable=False, unique=True, index=True)
    platform = db.Column(db.String(50), nullable=True)
    product_name = db.Column(db.String(500), nullable=True)
    current_price = db.Column(db.Float, default=0.0)
    rating = db.Column(db.Float, nullable=True)
    review_count = db.Column(db.Integer, default=0)
    last_checked = db.Column(db.DateTime, nullable=True, index=True)
    # ── Soft delete / dormant sayaç sistemi ──
    active_users_count = db.Column(db.Integer, default=0, index=True)
    is_dormant = db.Column(db.Boolean, default=False, index=True)
    created_at = db.Column(db.DateTime, default=get_tr_now)


class KeywordPool(db.Model):
    """(Platform, Keyword, URL) tekil SEO arama kaydı."""
    __tablename__ = 'keyword_pools'
    id = db.Column(db.Integer, primary_key=True)
    platform = db.Column(db.String(30), nullable=False, default='Trendyol')
    keyword = db.Column(db.String(200), nullable=False, index=True)
    target_url = db.Column(db.String(700), nullable=False)
    current_page = db.Column(db.Integer, default=0)
    current_rank = db.Column(db.Integer, default=0)
    last_checked = db.Column(db.DateTime, nullable=True, index=True)
    active_users_count = db.Column(db.Integer, default=0, index=True)
    is_dormant = db.Column(db.Boolean, default=False, index=True)
    created_at = db.Column(db.DateTime, default=get_tr_now)

    __table_args__ = (
        db.UniqueConstraint('platform', 'keyword', 'target_url', name='uq_pool_combo'),
    )


# ═══════════════════════════════════════════════════════════════════════════
# HOTFIX 1.91: Global Havuz Yardımcı Fonksiyonları
# ═══════════════════════════════════════════════════════════════════════════
def get_or_create_global_product(url, platform=None, product_name=None):
    """URL bazlı tekil GP yarat veya getir. active_users_count BU FONKSİYON
    İÇİNDE artırılmaz — bağlı TrackedProduct yaratıldığında çağıran arttırır."""
    if not url:
        return None
    gp = GlobalProduct.query.filter_by(url=url).first()
    if gp:
        # Var olan GP — dormant'sa uyandır (yeniden takip ediliyor)
        if gp.is_dormant:
            gp.is_dormant = False
        return gp
    gp = GlobalProduct(
        url=url,
        platform=platform,
        product_name=product_name,
        active_users_count=0,
        is_dormant=False,
    )
    db.session.add(gp)
    db.session.flush()
    return gp


def get_or_create_keyword_pool(platform, keyword, target_url):
    """(platform, keyword, target_url) kombo için Pool yarat veya getir."""
    if not (keyword and target_url):
        return None
    pool = KeywordPool.query.filter_by(
        platform=platform or 'Trendyol',
        keyword=keyword,
        target_url=target_url,
    ).first()
    if pool:
        if pool.is_dormant:
            pool.is_dormant = False
        return pool
    pool = KeywordPool(
        platform=platform or 'Trendyol',
        keyword=keyword,
        target_url=target_url,
        active_users_count=0,
        is_dormant=False,
    )
    db.session.add(pool)
    db.session.flush()
    return pool


def attach_tracked_product_to_global(tp):
    """TP yaratıldıktan sonra çağrılır. GP'ye bağlar + sayacı artırır."""
    if not tp or not tp.url:
        return
    gp = get_or_create_global_product(tp.url, platform=tp.platform_name,
                                      product_name=tp.product_name)
    if not gp:
        return
    if tp.global_product_id != gp.id:
        tp.global_product_id = gp.id
    gp.active_users_count = (gp.active_users_count or 0) + 1
    gp.is_dormant = False


def attach_keyword_tracker_to_pool(kt):
    """KT yaratıldıktan sonra çağrılır. Pool'a bağlar + sayacı artırır."""
    if not kt or not kt.keyword or not kt.target_url:
        return
    pool = get_or_create_keyword_pool(kt.platform, kt.keyword, kt.target_url)
    if not pool:
        return
    if kt.pool_id != pool.id:
        kt.pool_id = pool.id
    pool.active_users_count = (pool.active_users_count or 0) + 1
    pool.is_dormant = False


def detach_tracked_product_from_global(tp):
    """TP silinmeden ÖNCE çağrılır. GP sayacını azaltır; 0'a düşerse dormant."""
    if not tp or not tp.global_product_id:
        return
    gp = GlobalProduct.query.get(tp.global_product_id)
    if not gp:
        return
    gp.active_users_count = max(0, (gp.active_users_count or 0) - 1)
    if gp.active_users_count == 0:
        gp.is_dormant = True


def detach_keyword_tracker_from_pool(kt):
    """KT silinmeden ÖNCE çağrılır. Pool sayacını azaltır; 0 → dormant."""
    if not kt or not kt.pool_id:
        return
    pool = KeywordPool.query.get(kt.pool_id)
    if not pool:
        return
    pool.active_users_count = max(0, (pool.active_users_count or 0) - 1)
    if pool.active_users_count == 0:
        pool.is_dormant = True


class AiReport(db.Model):
    __tablename__ = 'ai_reports'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    sector = db.Column(db.String(100), nullable=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=get_tr_now, index=True)
    # HOTFIX 1.45: Etkileşimli YZ Danışman — grup filtresi ve özel prompt desteği
    group_id = db.Column(db.String(50), nullable=True)       # hangi grup analiz edildi (None = tüm portföy)
    group_name = db.Column(db.String(255), nullable=True)    # grup görünen adı (base ürün ismi)
    custom_prompt = db.Column(db.Text, nullable=True)        # kullanıcının özel sorusu/talebi

    user = db.relationship('User', backref=db.backref('ai_reports', lazy=True, cascade='all, delete-orphan', order_by='AiReport.created_at.desc()'))


def init_db(app):
    """Initialize the database with default data."""
    with app.app_context():
        db.create_all()
        # ── FAZ 10B: ALTER TABLE migration blokları kaldırıldı ─────────────
        # Eski (Faz 0-9): 25+ kolon için elle yazılmış try/except ALTER TABLE
        # blokları (yaklaşık 380 satır). İdempotent ama: hata mesajları kayboldu,
        # yeni dev için okunamaz hale geldi, "şu an hangi state?" sorusu imkânsız.
        #
        # Yeni (Faz 10B): Alembic devraldı.
        #   • Yeni kolon eklemek: model değiştir + `alembic revision --autogenerate -m "..."`
        #   • Üretime uygulamak:  `alembic upgrade head` (önce backup_db.py!)
        #   • Mevcut durum:       `alembic current`
        #   • Geçmiş:             `alembic history`
        #
        # init_db artık SADECE:
        #   1) Yeni DB'de tablo yaratma (db.create_all → models metadata'sından)
        #   2) Plan seed/update (kod-kaynağı plan tanımlarıyla sync)
        #   3) Env'den admin yaratma (ADMIN_EMAIL/PASSWORD varsa)
        #   4) Default Setting'ler (approval_mode, free_trial_days)
        #   5) Hayalet TrackedProduct cleanup (Zafiyet Radarı geçişinden kalan)
        #
        # NOT: Üretim DB'sinde init_db ÇALIŞTIRMA (zaten çalıştırılmıştı).
        #      Sadece yeni feature kolonu eklerken alembic migration üret + uygula.

        # Create or Update plans to match code definitions
        plans_data = [
            {
                'name': 'trial', 'display_name': 'Ücretsiz Deneme',
                'max_requests': 10, 'max_tracked_products': 10, 'period_type': 'weekly',
                'features': json.dumps(['price', 'review', 'combined']),
                'price_monthly': 0, 'sort_order': 0
            },
            {
                'name': 'starter', 'display_name': 'Başlangıç',
                'max_requests': 15, 'max_tracked_products': 50, 'period_type': 'weekly',
                'features': json.dumps(['price', 'review', 'combined']),
                'price_monthly': 499, 'sort_order': 1
            },
            {
                'name': 'professional', 'display_name': 'Profesyonel',
                'max_requests': 50, 'max_tracked_products': 250, 'period_type': 'daily',
                'features': json.dumps(['price', 'review', 'combined', 'radar']),
                'price_monthly': 1499, 'sort_order': 2
            },
            {
                'name': 'enterprise', 'display_name': 'Kurumsal',
                'max_requests': 0, 'max_tracked_products': 0, 'period_type': 'daily',
                'features': json.dumps(['price', 'review', 'combined', 'radar', 'ai_consultant']),
                'price_monthly': 4999, 'sort_order': 3
            },
        ]
        
        for p_data in plans_data:
            plan = Plan.query.filter_by(name=p_data['name']).first()
            if plan:
                # Update existing plan
                plan.display_name = p_data['display_name']
                plan.max_requests = p_data['max_requests']
                plan.max_tracked_products = p_data['max_tracked_products']
                plan.period_type = p_data['period_type']
                plan.features = p_data['features']
                plan.price_monthly = p_data['price_monthly']
                plan.sort_order = p_data['sort_order']
            else:
                # Create new plan
                new_plan = Plan(**p_data)
                db.session.add(new_plan)

        # ── FAZ 10A: Default admin SADECE env'den oluşturulur ──────────────
        # Eski davranış: hard-coded admin@bmk.com / bmk2024admin → güvenlik açığı,
        # production'a deploy edilirse internetteki herkes admin paneline girer.
        #
        # Yeni davranış:
        #   • Hiç admin yoksa VE ADMIN_EMAIL + ADMIN_PASSWORD env varsa → admin yarat
        #   • Hiç admin yoksa VE env eksikse → log uyarısı (uygulama çalışmaya devam)
        #     Admin yaratmak için: python scripts/create_admin.py
        #
        # KURAL: Mevcut hard-coded admin@bmk.com hesabını MANUEL silmeyi unutma:
        #   psql $DATABASE_URL -c "DELETE FROM users WHERE email='admin@bmk.com';"
        import os as _os
        if User.query.filter_by(is_admin=True).count() == 0:
            admin_email = (_os.environ.get('ADMIN_EMAIL') or '').strip().lower()
            admin_password = _os.environ.get('ADMIN_PASSWORD') or ''
            if admin_email and admin_password:
                admin = User(
                    email=admin_email,
                    full_name=_os.environ.get('ADMIN_FULL_NAME', 'BMK Admin'),
                    company=_os.environ.get('ADMIN_COMPANY', 'BMK'),
                    is_admin=True,
                    is_active=True,
                    is_approved=True,
                )
                admin.set_password(admin_password)
                db.session.add(admin)
                log.info("[init_db] Admin hesabı env'den oluşturuldu: %s", admin_email)
            else:
                log.warning(
                    "[init_db] Hiç admin kullanıcı yok ve ADMIN_EMAIL/ADMIN_PASSWORD "
                    "env değişkenleri tanımlı değil. Admin oluşturmak için: "
                    "1) .env'e ADMIN_EMAIL ve ADMIN_PASSWORD ekleyin, "
                    "2) python scripts/create_admin.py çalıştırın."
                )

        # Default settings (Faz 10A: groq_api_key kaldırıldı — sadece .env'den okunur)
        if Setting.query.count() == 0:
            Setting.set('approval_mode', 'manual')
            Setting.set('free_trial_days', '14')

        db.session.commit()

        # HOTFIX 1.24: Hayalet / öksüz kayıt temizliği (one-time + her başlangıçta).
        # Zafiyet Radarı kalıcı olarak devre dışı olduğundan:
        #   is_price_tracked = False  →  bu kayıt artık HİÇBİR aktif takipte değil.
        #   Bu kayıtları ve CASCADE ilişkilerini (PriceHistory, PriceAlert, vb.) sil.
        try:
            orphans = TrackedProduct.query.filter(
                TrackedProduct.is_price_tracked == False
            ).all()
            if orphans:
                log.info(f"[DB Cleanup] {len(orphans)} hayalet kayıt bulundu — siliniyor...")
                for orph in orphans:
                    db.session.delete(orph)
                db.session.commit()
                log.info("[DB Cleanup] Hayalet kayıtlar temizlendi.")
            else:
                log.info("[DB Cleanup] Hayalet kayıt yok, veritabanı temiz.")
        except Exception as cleanup_err:
            db.session.rollback()
            log.info(f"[DB Cleanup] Temizlik hatası (devam ediliyor): {cleanup_err}")
