"""Small, escaped SVG assets over frozen data, shared by screen and print."""
import math
from html import escape

COLORS=['#087f83','#a66a32','#75899b']


def number(value):
    if value is None:return '—'
    if isinstance(value,bool):return '是' if value else '否'
    try:
        n=float(value)
        return f'{n:,.2f}'.rstrip('0').rstrip('.') if not n.is_integer() else f'{int(n):,}'
    except (TypeError,ValueError):return str(value)


def svg(chart,compact=False):
    width=350 if compact else 840
    labels=chart['labels'];series=chart['series'];bar=chart['kind']=='bar'
    height=max(200,len(labels)*38+70) if bar else 310
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(chart["title"],quote=True)}">',
           f'<title>{escape(chart["title"])}；单位：{escape(chart["unit"])}；完整数值见数据表。</title>',
           '<rect width="100%" height="100%" fill="#fff"/>']
    def text(x,y,value,size=12,anchor='start',color='#486577'):
        parts.append(f'<text x="{x:.2f}" y="{y:.2f}" text-anchor="{anchor}" font-family="Microsoft YaHei,Arial,sans-serif" font-size="{size}" fill="{color}">{escape(str(value))}</text>')
    if not labels or not any(v is not None for s in series for v in s['values']):
        text(22,70,'此范围没有可绘制的完整数值。')
        parts.append('</svg>');return ''.join(parts)
    if bar:
        maximum=max([float(v) for v in series[0]['values'] if v is not None]+[1])
        left=22 if compact else 225;right=64;plot=width-left-right
        for i,(label,value) in enumerate(zip(labels,series[0]['values'])):
            y=40+i*38
            if compact:
                text(left,y,str(label)[:27],11)
                y+=7
            else:text(left-12,y+11,str(label)[:27],12,'end')
            if value is None:continue
            size=float(value)/maximum*plot
            parts.append(f'<rect x="{left}" y="{y}" width="{size:.2f}" height="13" rx="2" fill="{COLORS[0]}"><title>{escape(str(label))}：{number(value)}</title></rect>')
            text(left+size+6,y+11,number(value),11)
        text(left,height-16,chart['unit'],11)
    else:
        left=55;right=23;top=20;bottom=236;plot=width-left-right
        raw=[float(v) for s in series for v in s['values'] if v is not None]
        raw += [float(v) for v in chart.get('upper',[]) if v is not None]
        maximum=max(raw+[1])*1.12
        def x(i):return left+plot*i/max(1,len(labels)-1)
        def y(value):return bottom-(bottom-top)*float(value)/maximum
        for tick in range(5):
            value=maximum*tick/4;yy=y(value)
            parts.append(f'<line x1="{left}" x2="{width-right}" y1="{yy:.2f}" y2="{yy:.2f}" stroke="#e0e9ed"/>')
            label=number(round(value)) if maximum>=10 else number(round(value,1))
            if len(label)>7:label=number(value/1000)+'千'
            text(left-7,yy+4,label,10,'end')
        count=3 if compact else 7
        indices=sorted(set(round((len(labels)-1)*i/max(1,count-1)) for i in range(count)))
        for i in indices:
            label=str(labels[i])
            if len(label)>11:label=label[:10]
            text(x(i),bottom+20,label,10,'start' if i==0 else 'end' if i==len(labels)-1 else 'middle')
        if chart.get('lower'):
            groups=[];group=[]
            for i,(lo,hi) in enumerate(zip(chart['lower'],chart['upper'])):
                if lo is None or hi is None:
                    if group:groups.append(group);group=[]
                else:group.append((i,lo,hi))
            if group:groups.append(group)
            for group in groups:
                points=[(x(i),y(lo)) for i,lo,hi in group]+[(x(i),y(hi)) for i,lo,hi in reversed(group)]
                parts.append('<polygon points="'+' '.join(f'{a:.2f},{b:.2f}' for a,b in points)+'" fill="#b9854e" fill-opacity=".20"/>')
        for j,s in enumerate(series):
            points=[];groups=[]
            for i,value in enumerate(s['values']):
                if value is None:
                    if points:groups.append(points);points=[]
                else:points.append((i,float(value)))
            if points:groups.append(points)
            for group in groups:
                coords=' '.join(f'{x(i):.2f},{y(v):.2f}' for i,v in group)
                parts.append(f'<polyline points="{coords}" fill="none" stroke="{COLORS[j%3]}" stroke-width="2"'+(' stroke-dasharray="5,3"' if j else '')+'/>')
                if len(labels)<=80:
                    for i,v in group:
                        parts.append(f'<circle cx="{x(i):.2f}" cy="{y(v):.2f}" r="2" fill="{COLORS[j%3]}"><title>{escape(str(labels[i]))}：{escape(s["name"])} {number(v)}</title></circle>')
            legend_x=20+j*(155 if compact else 240)
            parts.append(f'<line x1="{legend_x}" x2="{legend_x+21}" y1="279" y2="279" stroke="{COLORS[j%3]}" stroke-width="2"'+(' stroke-dasharray="5,3"' if j else '')+'/>')
            text(legend_x+27,283,s['name'],11)
        text(left,14,chart['unit'],11)
        if chart.get('lower'):text(20,303,'浅赭色：经验误差参考带；样本不足处留空。',10)
    parts.append('</svg>')
    return ''.join(parts)


def make_figures(charts):
    return [dict(title=c['title'],note=c['note'],wide=svg(c),narrow=svg(c,True)) for c in charts]
