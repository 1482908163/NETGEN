"""Coverage and cost checks for ordered point-star mutation waves."""
import math

FIELDS=('calls','scanned_edges','candidates','attempts','applied','waves','parallel_waves',
        'parallel_attempts','planning_seconds','evaluation_seconds','commit_seconds',
        'seconds','verified_waves','verified_attempts','mismatches','max_wave_size')

def validate(metrics,wave,verify,threads):
    m={key:metrics['combine_commit_'+key] for key in FIELDS}
    for key,v in m.items():
        if not math.isfinite(v) or v<0 or ('seconds' not in key and v!=int(v)):
            raise ValueError('invalid combine commit metric: '+key)
    if not m['applied']<=m['attempts']==m['candidates']<=m['scanned_edges']:
        raise ValueError('combine work conservation failed')
    if (m['candidates'] or m['scanned_edges']) and not m['calls']:
        raise ValueError('combine candidates without calls')
    if m['mismatches'] or m['verified_waves']!=(m['waves'] if verify else 0) or m['verified_attempts']!=(m['attempts'] if verify else 0):
        raise ValueError('combine exact serial replay coverage failed')
    if wave:
        if not (m['parallel_waves']<=m['waves']<=m['attempts'] and
                2*m['parallel_waves']<=m['parallel_attempts']<=m['attempts'] and
                m['parallel_attempts']<=threads*m['parallel_waves']):
            raise ValueError('combine wave conservation failed')
        if (m['attempts']==0)!=(m['waves']==0) or (m['waves']==0)!=(m['max_wave_size']==0):
            raise ValueError('combine wave missing work')
        if m['max_wave_size']>threads or (m['max_wave_size']>1)!=(m['parallel_waves']>0):
            raise ValueError('combine wave exceeds available team')
        if m['attempts']!=m['parallel_attempts']+m['waves']-m['parallel_waves']:
            raise ValueError('combine singleton/parallel attempt count mismatch')
    elif any(m[k] for k in ('waves','parallel_waves','parallel_attempts','planning_seconds','max_wave_size')):
        raise ValueError('serial combine control unexpectedly formed waves')
    if m['planning_seconds']>m['commit_seconds']+1e-6 or m['evaluation_seconds']+m['commit_seconds']>m['seconds']+1e-6 or m['seconds']>1.05*metrics['kernel_optimization_seconds']+1e-3:
        raise ValueError('combine timing outside final optimization')
