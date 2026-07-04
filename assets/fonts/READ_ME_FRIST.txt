لازم تحط هنا ملف خط عربي حقيقي (.ttf) عشان يقدر البوت يرسم النصوص العربية على بطاقة الأغنية.

الخطوات:
1. روح لموقع Google Fonts: https://fonts.google.com/noto/specimen/Noto+Naskh+Arabic
   (أو أي خط عربي ثاني تحبه، مثل "Cairo": https://fonts.google.com/specimen/Cairo)
2. حمّل ملف الخط (Download family)
3. فك الضغط، وخذ ملف بامتداد .ttf مثل:
   NotoNaskhArabic-Regular.ttf
4. غيّر اسمه إلى: arabic.ttf
5. حطه بهذا المسار بالضبط: assets/fonts/arabic.ttf

لو تبي خط عريض منفصل للعناوين (اختياري)، حط نسخة Bold باسم: arabic-bold.ttf
وضبط متغير FONT_BOLD_PATH في Railway على: assets/fonts/arabic-bold.ttf

لو ما حطيت الخط، البوت بيشتغل بس بخط افتراضي بسيط ما يدعم العربي بشكل جيد (حروف مقطوعة).
