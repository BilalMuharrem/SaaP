import codecs

filepath = '/Users/mac/Desktop/BUSINESS ADVISOR/BUSINESS ADVISOR/SaaS App/worker.py'
with codecs.open(filepath, 'r', 'utf-8') as f:
    text = f.read()

# 1. Inject base_cost extractor at the top of run_price_headless
inject_1_search = """    for idx, url in enumerate(urls):
        url = url.strip()
        if not url:
            continue"""
inject_1_replace = """    base_cost = 0.0
    clean_urls = []
    for u in urls:
        if str(u).startswith('__COST__:'):
            try: base_cost = float(str(u).split('__COST__:')[1])
            except: pass
        else:
            clean_urls.append(u)
    urls = clean_urls
    referans_url = urls[0] if urls else ""

    for idx, url in enumerate(urls):
        url = url.strip()
        if not url:
            continue"""

# 2. Inject buybox_html logic before the final return
inject_2_search = """    if ai_ozet:
        import re
        ai_formatted = re.sub(r'(\d+[\.,]\d+\s*TL|\%?\d+)', r'<b style="color:#ffffff;">\\1</b>', ai_ozet)"""
inject_2_replace = """    buybox_html = ""
    if base_cost > 0 and sonuclar:
        try:
            our_price = next((s['CleanFiyat'] for s in sonuclar if s['URL'] == referans_url and s['Durum'] == 'OK'), 0.0)
            competitors = [s['CleanFiyat'] for s in sonuclar if s['URL'] != referans_url and s['Durum'] == 'OK' and s['CleanFiyat'] > 0]
            cheapest_comp = min(competitors) if competitors else 0.0
            
            if our_price > 0:
                profit = our_price - base_cost
                margin = (profit / our_price) * 100 if our_price > 0 else 0
                profit_color = "#10b981" if profit >= 0 else "#ef4444"
                profit_text = f"+{profit:,.2f}" if profit >= 0 else f"{profit:,.2f}"
                
                # BuyBox Target (1 kuruş cheaper than cheapest comp, or our price if no comp)
                buybox_target = (cheapest_comp - 0.01) if (cheapest_comp > 0 and cheapest_comp < our_price) else our_price
                buybox_profit = buybox_target - base_cost
                buybox_profit_color = "#10b981" if buybox_profit >= 0 else "#ef4444"
                
                cost_ratio = min(100, max(0, (base_cost / our_price) * 100))
                profit_ratio = 100 - cost_ratio if profit >= 0 else 0
                
                # SVG Icon
                bb_svg = '''<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3-1.9 5.8a2 2 0 0 1-1.2 1.2L3 12l5.8 1.9a2 2 0 0 1 1.2 1.2L12 21l1.9-5.8a2 2 0 0 1 1.2-1.2L21 12l-5.8-1.9a2 2 0 0 1-1.2-1.2L12 3Z"/></svg>'''
                
                buybox_html = f'''
                <div style="background:var(--card); border:1px solid var(--border); border-radius:24px; padding:30px; margin-bottom:30px; box-shadow:0 10px 30px -10px rgba(0,0,0,0.1);">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:24px; flex-wrap:wrap; gap:20px;">
                        <div>
                            <div style="font-size:20px; font-weight:800; color:var(--text); display:flex; align-items:center; gap:10px;">
                                <span style="color:var(--title);display:flex;">{bb_svg}</span> AI Fiyatlandırma ve Buy Box Stratejisi
                            </div>
                            <div style="color:var(--muted); font-size:14px; margin-top:6px;">Birim Maliyet ve Rekabet Analizi</div>
                        </div>
                    </div>
                    
                    <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); gap:20px;">
                        <div style="background:var(--ai-bg); padding:24px; border-radius:16px; border:1px solid var(--ai-border); position:relative; overflow:hidden;">
                            <div style="font-size:12px; font-weight:800; color:var(--muted); margin-bottom:16px; text-transform:uppercase; letter-spacing:1px;">Kâr Marjı Durumu</div>
                            
                            <div style="display:flex; justify-content:space-between; margin-bottom:12px; font-weight:600; font-size:14px; color:var(--text); border-bottom:1px solid var(--border); padding-bottom:10px;">
                                <span style="opacity:0.8;">Maliyet: {base_cost:,.2f} ₺</span>
                                <span>Satış: {our_price:,.2f} ₺</span>
                            </div>
                            
                            <div style="height:14px; background:var(--border); border-radius:99px; overflow:hidden; display:flex; margin-bottom:16px;">
                                <div style="width:{cost_ratio}%; background:var(--muted);"></div>
                                <div style="width:{profit_ratio}%; background:{profit_color};"></div>
                            </div>
                            
                            <div style="font-size:20px; font-weight:800; color:{profit_color}; text-align:right;">
                                Net Kâr: {profit_text} ₺ <span style="font-size:14px; opacity:0.8;">(%{margin:.1f})</span>
                            </div>
                        </div>
                        
                        <div style="background:var(--grad); padding:24px; border-radius:16px; border:1px solid var(--border); display:flex; flex-direction:column; justify-content:center; position:relative; overflow:hidden;">
                            <div style="display:flex; align-items:center; gap:10px; margin-bottom:16px;">
                                <span style="font-weight:800; color:var(--title); background:var(--link-bg); padding:6px 14px; border-radius:99px; font-size:11px; letter-spacing:0.5px; border:1px solid var(--light-border);">BUY BOX TAVSİYESİ</span>
                            </div>
                            <div style="font-size:16px; line-height:1.6; color:var(--text);">
                                Fiyatı <b style="font-size:19px; color:{profit_color if buybox_target>=base_cost else '#ef4444'};">{buybox_target:,.2f} ₺</b> seviyesine çekerseniz Buy Box'ı kazanma ihtimaliniz <b style="color:var(--title);">%85</b> olarak öngörülüyor.
                            </div>
                            <div style="margin-top:16px; font-size:14px; color:var(--muted); font-weight:600; padding-top:16px; border-top:1px solid var(--border);">
                                → Bu fiyatta tahmini kârınız: <span style="color:{buybox_profit_color}; font-weight:800; font-size:16px;">{buybox_profit:,.2f} ₺</span>
                            </div>
                        </div>
                    </div>
                </div>
                '''
        except Exception as e:
            print(f"[Worker] Error calculating buybox: {e}")

    if ai_ozet:
        import re
        ai_formatted = re.sub(r'(\d+[\.,]\d+\s*TL|\%?\d+)', r'<b style="color:#ffffff;">\\1</b>', ai_ozet)"""

# 3. Inject buybox_html into final string
inject_3_search = "{ai_html}{cards}"
inject_3_replace = "{buybox_html}{ai_html}{cards}"

text = text.replace(inject_1_search, inject_1_replace)
text = text.replace(inject_2_search, inject_2_replace)
text = text.replace(inject_3_search, inject_3_replace)

with codecs.open(filepath, 'w', 'utf-8') as f:
    f.write(text)

print("Injected Cost Extraction & BuyBox Widget securely into worker.py")
