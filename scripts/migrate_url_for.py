"""
scripts/migrate_url_for.py — Template url_for endpoint adlarını
blueprint-prefixed forma toplu günceller.

KULLANIM:  python scripts/migrate_url_for.py
       Veya: ./.venv/bin/python scripts/migrate_url_for.py

Eski (düz) → yeni (blueprint.prefixed) eşleme tek bir sözlükte. Script:
  • templates/ altındaki tüm .html dosyalarını tarar
  • url_for('eski_isim'...)  →  url_for('yeni.isim'...)  şeklinde günceller
  • Tek tırnak ve çift tırnak için iki ayrı pattern
  • Sadece tam isim eşleşmesi (parça eşleşme YOK)
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(ROOT, 'templates')

# Eski düz endpoint adı → yeni blueprint.endpoint
ENDPOINT_MAP = {
    # auth
    'index': 'auth.index',
    'login': 'auth.login',
    'register': 'auth.register',
    'logout': 'auth.logout',
    # dashboard
    'dashboard': 'dashboard.dashboard',
    'history': 'dashboard.history',
    # jobs
    'new_request': 'jobs.new_request',
    'job_status': 'jobs.job_status',
    'cancel_job': 'jobs.cancel_job',
    'job_report': 'jobs.job_report',
    'api_job_status': 'jobs.api_job_status',
    'api_system_status': 'jobs.api_system_status',
    'api_dashboard_jobs_status': 'jobs.api_dashboard_jobs_status',
    # tracked
    'tracked_products': 'tracked.tracked_products',
    'export_tracked_excel': 'tracked.export_tracked_excel',
    'export_tracked_pdf': 'tracked.export_tracked_pdf',
    'delete_tracked_product': 'tracked.delete_tracked_product',
    'rename_tracked_group': 'tracked.rename_tracked_group',
    'delete_tracked_group': 'tracked.delete_tracked_group',
    'add_to_tracked_group': 'tracked.add_to_tracked_group',
    'update_tracked_group_cost': 'tracked.update_tracked_group_cost',
    'add_price_alert': 'tracked.add_price_alert',
    # 'zafiyet_radari_list', 'zafiyet_radari' → Faz 3'te kaldırıldı
    # seo
    'seo_tracker': 'seo.seo_tracker',
    'seo_tracker_delete': 'seo.seo_tracker_delete',
    'seo_tracker_refresh': 'seo.seo_tracker_refresh',
    'seo_graph': 'seo.seo_graph',
    'seo_graph_delete_group': 'seo.seo_graph_delete_group',
    'seo_graph_delete_tracker': 'seo.seo_graph_delete_tracker',
    'start_group_seo': 'seo.start_group_seo',
    'api_generate_seo_tips': 'seo.api_generate_seo_tips',
    # ai_consultant
    'ai_consultant': 'ai_consultant.ai_consultant',
    'ai_consultant_report_standalone': 'ai_consultant.ai_consultant_report_standalone',
    'download_strategy_pdf': 'ai_consultant.download_strategy_pdf',
    'generate_ai_consultant': 'ai_consultant.generate_ai_consultant',
    # notifications
    'notifications': 'notifications.notifications',
    'api_unread_notifications': 'notifications.api_unread_notifications',
    'api_mark_category_read': 'notifications.api_mark_category_read',
    'read_all_notifications': 'notifications.read_all_notifications',
    'notification_open': 'notifications.notification_open',
    'clear_notifications_category': 'notifications.clear_notifications_category',
    # plans
    'user_plans': 'plans.user_plans',
    # admin
    'admin_dashboard': 'admin.admin_dashboard',
    'admin_customers': 'admin.admin_customers',
    'admin_approve_customer': 'admin.admin_approve_customer',
    'admin_toggle_customer': 'admin.admin_toggle_customer',
    'admin_change_plan': 'admin.admin_change_plan',
    'admin_jobs': 'admin.admin_jobs',
    'admin_tracking': 'admin.admin_tracking',
    'admin_plans': 'admin.admin_plans',
    'admin_edit_plan': 'admin.admin_edit_plan',
    'admin_settings': 'admin.admin_settings',
}


def migrate_file(path):
    """Tek bir template dosyasında url_for çağrılarını günceller.
    Geri dönüş: değiştirilen toplam çağrı sayısı."""
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    original = content
    total_changes = 0

    for old, new in ENDPOINT_MAP.items():
        # Tek tırnak: url_for('old'
        pat_single = re.compile(rf"url_for\('{re.escape(old)}'(?=[,\)])")
        new_content, n1 = pat_single.subn(f"url_for('{new}'", content)
        # Çift tırnak: url_for("old"
        pat_double = re.compile(rf'url_for\("{re.escape(old)}"(?=[,\)])')
        new_content, n2 = pat_double.subn(f'url_for("{new}"', new_content)
        if n1 + n2 > 0:
            content = new_content
            total_changes += n1 + n2

    if content != original:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
    return total_changes


def main():
    total_files = 0
    total_changes = 0
    for root, _, files in os.walk(TEMPLATES_DIR):
        for fname in files:
            if not fname.endswith('.html'):
                continue
            path = os.path.join(root, fname)
            n = migrate_file(path)
            if n > 0:
                print(f"  {os.path.relpath(path, ROOT)}: {n} değişiklik")
                total_files += 1
                total_changes += n

    print(f"\n✓ Toplam {total_changes} url_for güncellendi, {total_files} dosya değişti.")


if __name__ == '__main__':
    main()
