import glob

replacements = {
    # Sidebar
    '<span class="icon">📊</span>': '<i data-lucide="layout-dashboard" class="icon" width="20" height="20"></i>',
    '<span class="icon">➕</span>': '<i data-lucide="plus-circle" class="icon" width="20" height="20"></i>',
    '<span class="icon">🎯</span>': '<i data-lucide="crosshair" class="icon" width="20" height="20"></i>',
    '<span class="icon">💎</span>': '<i data-lucide="gem" class="icon" width="20" height="20"></i>',
    '<span class="icon">🔔</span>': '<i data-lucide="bell" class="icon" width="20" height="20"></i>',
    '<span class="icon">🚪</span>': '<i data-lucide="log-out" class="icon" width="20" height="20"></i>',
    
    # Stat cards
    '<div class="stat-icon">📊</div>': '<div class="stat-icon" style="color:var(--accent);"><i data-lucide="bar-chart-2" stroke-width="2.5"></i></div>',
    '<div class="stat-icon">✅</div>': '<div class="stat-icon" style="color:var(--success);"><i data-lucide="check-circle-2" stroke-width="2.5"></i></div>',
    '<div class="stat-icon">⏳</div>': '<div class="stat-icon" style="color:var(--warning);"><i data-lucide="hourglass" stroke-width="2.5"></i></div>',
    
    # New Request radio options
    '<div class="icon">💰</div>': '<div class="icon" style="color:var(--accent); margin-bottom:12px;"><i data-lucide="coins" width="32" height="32" stroke-width="1.5"></i></div>',
    '<div class="icon">💬</div>': '<div class="icon" style="color:var(--accent); margin-bottom:12px;"><i data-lucide="message-square" width="32" height="32" stroke-width="1.5"></i></div>',
    '<div class="icon">🔄</div>': '<div class="icon" style="color:var(--accent); margin-bottom:12px;"><i data-lucide="refresh-cw" width="32" height="32" stroke-width="1.5"></i></div>',
    '<div class="icon">🤖</div>': '<div class="icon" style="color:var(--accent); margin-bottom:12px;"><i data-lucide="bot" width="32" height="32" stroke-width="1.5"></i></div>',

    # Table Icons
    '💰 Fiyat Analizi': '<i data-lucide="coins" style="width:14px; height:14px; margin-right:6px; vertical-align:-2px; display:inline-block;"></i> Fiyat Analizi',
    '💬 Yorum Analizi': '<i data-lucide="message-square" style="width:14px; height:14px; margin-right:6px; vertical-align:-2px; display:inline-block;"></i> Yorum Analizi',
    '🔄 Kombine Analiz': '<i data-lucide="refresh-cw" style="width:14px; height:14px; margin-right:6px; vertical-align:-2px; display:inline-block;"></i> Kombine Analiz',
    '🤖 Akıllı Analiz': '<i data-lucide="bot" style="width:14px; height:14px; margin-right:6px; vertical-align:-2px; display:inline-block;"></i> Akıllı Analiz',
    
    # Other Emojis
    '💰 Fiyat Tablosu': '<i data-lucide="coins" style="width:16px; height:16px; margin-right:8px; vertical-align:middle; display:inline-block;"></i> Fiyat Tablosu',
    '💬 Genel Kanı': '<i data-lucide="message-circle" style="width:16px; height:16px; margin-right:6px; vertical-align:-2px; color: var(--accent); display:inline-block;"></i> Genel Kanı',
    '🌟 Başarılı Yönler': '<i data-lucide="star" style="width:16px; height:16px; margin-right:6px; vertical-align:-2px; color: var(--warning); display:inline-block;"></i> Başarılı Yönler',
    '⚠️ Kritik Şikayetler': '<i data-lucide="alert-triangle" style="width:16px; height:16px; margin-right:6px; vertical-align:-2px; color: var(--danger); display:inline-block;"></i> Kritik Şikayetler',
    'Hoş Geldiniz,': '<i data-lucide="sparkles" style="width:28px; height:28px; margin-right:12px; vertical-align:-4px; color: var(--accent); display:inline-block;"></i> Hoş Geldiniz,',
    '👋': ''
}

import re

for filepath in glob.glob('/Users/mac/Desktop/BUSINESS ADVISOR/BUSINESS ADVISOR/SaaS App/templates/*.html'):
    with open(filepath, 'r') as f:
        content = f.read()

    changed = False
    for old, new in replacements.items():
        if old in content:
            content = content.replace(old, new)
            changed = True
            
    # Remove any standalone emojis left dynamically or inject scripts
    if '</body>' in content and 'lucide.createIcons()' not in content:
        content = content.replace('</body>', '<script src="https://unpkg.com/lucide@latest"></script>\\n<script>lucide.createIcons();</script>\\n</body>')
        changed = True

    if changed:
        with open(filepath, 'w') as f:
            f.write(content)
        print(f"Updated {filepath}")
