"""
utils/analytics.py — Veri analiz yardımcıları.

İçindekiler:
    extract_review_insights_from_jobs(user_id, target_urls)
        Geçmiş 'review' / 'combined' Job'larından kayıtlı HTML'i BeautifulSoup
        ile parse ederek ürün URL bazlı sentiment metinleri (praises/complaints/
        general) döndürür. YZ Danışmanı promptuna geçilir.
"""
import logging
from bs4 import BeautifulSoup

from models import Job

log = logging.getLogger(__name__)


def extract_review_insights_from_jobs(user_id, target_urls):
    """
    Kullanıcının geçmiş Job'larındaki kayıtlı HTML'i parse ederek, belirli
    URL'ler için BAŞARILI YÖNLER / KRİTİK ŞİKAYETLER / GENEL KANI metinlerini
    yapısal sözlük olarak döndürür.

    Returns:
        { "<url>": { "praises": [..], "complaints": [..], "general": ".." } }

    Aynı URL için en yeni Job'tan başlanır, ikinci kez parse edilmez.
    """
    insights = {}
    if not target_urls:
        return insights

    target_set = set(target_urls)
    try:
        jobs = (Job.query
                .filter(Job.user_id == user_id,
                        Job.status == 'completed',
                        Job.job_type.in_(('review', 'combined')))
                .order_by(Job.created_at.desc())
                .limit(40)
                .all())
    except Exception:
        log.exception("[YZ Danışman] Job sorgulama hatası")
        return insights

    for job in jobs:
        if not job.result_html:
            continue
        try:
            job_urls = set(job.get_urls(filter_metadata=True))
        except Exception:
            continue
        if not (job_urls & target_set):
            continue
        # Aranan URL'lerin hepsi zaten doluysa daha eski Job'a bakmaya gerek yok
        if all(u in insights for u in (job_urls & target_set)):
            continue

        try:
            soup = BeautifulSoup(job.result_html, 'lxml')
            for anchor in soup.find_all('a', href=True):
                href = anchor.get('href')
                if href not in target_set or href in insights:
                    continue
                # Bu URL'i içeren ürün kartını bul
                card = anchor
                for _ in range(10):
                    card = card.parent
                    if card is None:
                        break
                    if getattr(card, 'name', None) == 'div' and card.find(
                        string=lambda s: s and 'BAŞARILI YÖNLER' in s
                    ):
                        break
                if not card or getattr(card, 'name', None) != 'div':
                    continue

                praises, complaints, general = [], [], ""

                praise_label = card.find(string=lambda s: s and 'BAŞARILI YÖNLER' in s)
                if praise_label and praise_label.parent:
                    praise_block = praise_label.parent.find_next('ul')
                    if praise_block:
                        for li in praise_block.find_all('li'):
                            txt = li.get_text(separator=' ', strip=True).lstrip('•').strip()
                            if txt and len(txt) > 4:
                                praises.append(txt)

                complaint_label = card.find(string=lambda s: s and 'KRİTİK ŞİKAYETLER' in s)
                if complaint_label and complaint_label.parent:
                    complaint_block = complaint_label.parent.find_next('ul')
                    if complaint_block:
                        for li in complaint_block.find_all('li'):
                            txt = li.get_text(separator=' ', strip=True).lstrip('•').strip()
                            if txt and len(txt) > 4:
                                complaints.append(txt)

                gen_label = card.find(string=lambda s: s and 'GENEL KANI' in s)
                if gen_label and gen_label.parent and gen_label.parent.parent:
                    full = gen_label.parent.parent.get_text(separator=' ', strip=True)
                    parts = full.split('GENEL KANI:', 1)
                    if len(parts) > 1:
                        general = parts[1].strip()[:600]

                if praises or complaints or general:
                    insights[href] = {
                        "praises": praises[:5],
                        "complaints": complaints[:5],
                        "general": general,
                    }
        except Exception:
            log.exception("[YZ Danışman] Job #%s HTML parse hatası", job.id)
            continue

    return insights
