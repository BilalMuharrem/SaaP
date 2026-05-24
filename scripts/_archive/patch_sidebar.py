import os
import glob

html_files = glob.glob('templates/*.html')

new_link = """                <a href="{{ url_for('zafiyet_radari_list') }}" class="nav-link{% if request.endpoint == 'zafiyet_radari_list' or request.endpoint == 'zafiyet_radari' %} active{% endif %}">
                    <i data-lucide="radar" class="icon" width="20" height="20" style="color:#f59e0b;"></i> Zafiyet Radarı
                </a>"""

for file in html_files:
    if 'landing' in file or 'base' in file:
        continue
    try:
        with open(file, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        continue
    
    if '<aside class="sidebar">' not in content:
        continue
        
    if 'zafiyet_radari_list' in content:
        continue

    lines = content.split('\n')
    new_lines = []
    inserted = False
    
    for line in lines:
        new_lines.append(line)
        if 'href="{{ url_for(\'tracked_products\') }}"' in line and not inserted and 'Fiyat Takibi' in line:
            new_lines.append(new_link)
            inserted = True
            
    with open(file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(new_lines))
        
print("Patched.")
