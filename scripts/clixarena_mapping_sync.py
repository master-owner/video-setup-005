"""Remove only mappings whose individual file status is confirmed missing twice."""
import base64,json,os,re,time,urllib.request,urllib.error,urllib.parse
from clixarena_video import api,decrypt_mappings,encrypt_mappings,mask,NoRedirect


def missing_ids(ids,result):
    if not isinstance(result,dict):return set()
    return {fid for fid in ids if isinstance(result.get(fid),dict) and type(result[fid].get('status')) is int and result[fid]['status']==404}


def confirmed_missing(ids,check,pause=time.sleep):
    first=missing_ids(ids,check(ids))
    if not first:return set()
    pause(5)
    return first & missing_ids(first,check(sorted(first)))


def streamtape_id(row):
    providers=row.get('providers',{})
    if isinstance(providers,dict) and isinstance(providers.get('streamtape'),dict):return providers['streamtape'].get('file_id')
    return row.get('file_id')


HOSTS={'streamtape':'file_id','abyss':'abyss_id','streamhg':'streamhg_id','telegram':'telegram_id'}


class SyncError(ValueError):
    """Only fixed stage labels and numeric statuses belong in these messages."""


def bulk_guard(total,missing):
    if len(missing)>=5 and len(missing)/total>=0.25:
        raise SyncError('Bulk missing-file guard: unusually many references were reported missing')


def streamhg_deleted(body):
    if not isinstance(body,dict) or str(body.get('status'))!='200' or not isinstance(body.get('result'),list):
        raise SyncError('StreamHG deleted-files response could not be verified')
    return {r['file_code'] for r in body['result'] if isinstance(r,dict)
            and isinstance(r.get('file_code'),str) and r.get('deleted') and r.get('deleted_by')}


def host_ids(row):
    providers=row.get('providers',{})
    if not isinstance(providers,dict):raise ValueError('Invalid providers')
    result={}
    for host,legacy in HOSTS.items():
        entry=providers.get(host)
        if entry is not None and not isinstance(entry,dict):raise ValueError('Invalid provider')
        value=entry.get('file_id') if entry is not None else row.get(legacy)
        if value is not None:
            if not isinstance(value,str) or not re.fullmatch('[A-Za-z0-9_-]{1,128}',value):raise ValueError('Invalid file ID')
            result[host]=value
    return result


def prune(rows,missing):
    if isinstance(missing,set):missing={'streamtape':missing}
    kept=[]
    for row in rows:
        ids=host_ids(row)
        removed={host for host,fid in ids.items() if fid in missing.get(host,set())}
        if not removed:kept.append(row);continue
        item=dict(row)
        providers=dict(item.get('providers',{}))
        for host,fid in ids.items():
            if host in removed:
                item.pop(HOSTS[host],None);providers.pop(host,None)
            elif host not in providers:providers[host]={'file_id':fid}
        if not providers:continue
        item['providers']=providers
        if item.get('provider') in removed:item['provider']=next(iter(providers))
        kept.append(item)
    return kept


def streamhg_missing(ids,body):
    if not isinstance(body,dict) or str(body.get('status'))!='200' or not isinstance(body.get('result'),list):
        raise ValueError('Unconfirmed StreamHG response')
    return {row['file_code'] for row in body['result'] if isinstance(row,dict)
            and row.get('file_code') in ids and type(row.get('status')) is int and row['status']==404}


def read_json(url,allow_404=False,headers=None,payload=None,stage='Provider read-only request'):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    request_headers={'Accept':'application/json','User-Agent':'CLIXARENA-mapping-sync/2026.10.21'}
    request_headers.update(headers or {})
    if payload is not None:request_headers['Content-Type']='application/json'
    req=urllib.request.Request(url,headers=request_headers,
        data=json.dumps(payload).encode() if payload is not None else None,
        method='POST' if payload is not None else 'GET')
    try:
        response=opener.open(req,timeout=30)
    except urllib.error.HTTPError as error:
        if not allow_404 or error.code!=404:
            challenge='; provider challenge detected' if error.headers and error.headers.get('cf-mitigated')=='challenge' else ''
            raise SyncError(stage+': HTTP '+str(error.code)+challenge) from None
        response=error
    with response:
        status=response.code
        data=response.read(1024*1024+1)
        if len(data)>1024*1024:raise ValueError('Oversized provider response')
        return status,json.loads(data)


def abyss_auth():
    """Use documented login/Bearer auth when configured; never treat API keys as JWTs."""
    email,password=os.getenv('ABYSS_EMAIL',''),os.getenv('ABYSS_PASSWORD','')
    token=os.getenv('ABYSS_AUTH_TOKEN','')
    if email or password:
        if not email or not password:raise SyncError('Abyss login requires both ABYSS_EMAIL and ABYSS_PASSWORD')
        status,body=read_json('https://api.abyss.to/auth/login',payload={'email':email,'password':password},stage='Abyss login')
        if status not in (200,201) or not isinstance(body,dict):raise SyncError('Abyss login response could not be verified')
        token=body.get('token','')
        if not token:raise SyncError('Abyss login returned no token')
    if token:
        if not isinstance(token,str) or len(token)>16384 or any(ord(c)<33 or ord(c)>126 for c in token):
            raise SyncError('Abyss authentication token format is invalid')
        mask(token)
        return '',{'Authorization':'Bearer '+token}
    key=os.getenv('ABYSS_API_KEY','')
    if not key:raise SyncError('Abyss API key or login credentials are not configured')
    return '?key='+urllib.parse.quote(key,safe=''),{}


def abyss_account_verified(body):
    if not isinstance(body,dict):return False
    # /v1/about returns quota information, not an account profile.
    fields={'storageQuota':('limit','actual','usage'),
            'uploadQuota':('dailyUploadQuota','dailyUploadUsed','dailyUploadRemaining')}
    for group,names in fields.items():
        values=body.get(group)
        if not isinstance(values,dict) or any(type(values.get(name)) is not int or values[name]<0 for name in names):
            return False
    return type(body.get('maxUploadSize')) is int and body['maxUploadSize']>=0


def abyss_file_missing(fid,status,body):
    # A generic HTTP 404 (e.g. wrong route/protection page) is never deletion evidence.
    if status!=404 or not isinstance(body,dict):return False
    message=body.get('message',body.get('error',''))
    if not isinstance(message,str):return False
    msg=message.strip().lower().rstrip('.')
    return msg in ('file not found','video not found','file does not exist','video does not exist','not found') and body.get('id') in (fid,None)


def collect_missing(rows,pause=time.sleep):
    ids={host:sorted({host_ids(row)[host] for row in rows if host in host_ids(row)}) for host in HOSTS}
    missing={host:set() for host in HOSTS};failed=[]
    for host in HOSTS:
        if not ids[host]:continue
        try:
            if host=='streamtape':
                login,key=os.environ['STREAMTAPE_LOGIN'],os.environ['STREAMTAPE_KEY']
                if not login or not key:raise ValueError()
                def check(batch):return api('file/info',{'login':login,'key':key,'file':','.join(batch)})
                for offset in range(0,len(ids[host]),100):
                    missing[host].update(confirmed_missing(ids[host][offset:offset+100],check,pause))
            elif host=='streamhg':
                from clixarena_hosts import streamhg_api
                key=os.environ['STREAMHG_API_KEY']
                if not key:raise ValueError()
                account=streamhg_api('account/info',key)
                if not isinstance(account.get('result'),dict) or not account['result'].get('login'):
                    raise SyncError('StreamHG account identity could not be verified')
                candidates=set()
                for offset in range(0,len(ids[host]),100):
                    batch=ids[host][offset:offset+100]
                    first=streamhg_missing(batch,streamhg_api('file/info',key,{'file_code':','.join(batch)}))
                    if first:
                        pause(5)
                        candidates.update(first & streamhg_missing(first,streamhg_api('file/info',key,{'file_code':','.join(sorted(first))})))
                if candidates:
                    bulk_guard(len(ids[host]),candidates)
                    deleted=streamhg_deleted(streamhg_api('file/deleted',key,{'last':168}))
                    pause(5)
                    deleted &= streamhg_deleted(streamhg_api('file/deleted',key,{'last':168}))
                    missing[host]=candidates & deleted
                    if candidates-deleted:
                        raise SyncError('StreamHG file-info missing results were not confirmed by account deleted-files history')
            elif host=='telegram':
                # Telegram files in private storage channels do not expire or get deleted due to inactivity.
                print(host+' checked: '+str(len(ids[host]))+' file(s); confirmed missing: 0. No IDs or URLs logged.')
            else:
                query,headers=abyss_auth()
                status,body=read_json('https://api.abyss.to/v1/about'+query,headers=headers,stage='Abyss quota lookup')
                if status!=200 or not abyss_account_verified(body):
                    raise SyncError('Abyss quota API response format could not be verified (HTTP '+str(status)+')')
                for fid in ids[host]:
                    url='https://api.abyss.to/v1/files/'+fid+query
                    status,body=read_json(url,True,headers=headers,stage='Abyss file lookup')
                    if abyss_file_missing(fid,status,body):
                        pause(5)
                        status,body=read_json(url,True,headers=headers,stage='Abyss repeated file lookup')
                        if abyss_file_missing(fid,status,body):missing[host].add(fid)
                        elif status!=200 or not isinstance(body,dict) or body.get('id')!=fid:
                            raise SyncError('Abyss repeated file lookup could not be verified (HTTP '+str(status)+')')
                    elif status!=200:
                        raise SyncError('Abyss file lookup could not confirm a file-specific deletion (HTTP '+str(status)+')')
                    elif not isinstance(body,dict) or body.get('id')!=fid:
                        raise SyncError('Abyss file lookup returned an unverified file identity (HTTP 200)')
            bulk_guard(len(ids[host]),missing[host])
            print(host+' checked: '+str(len(ids[host]))+' file(s); confirmed missing: '+str(len(missing[host]))+'. No IDs or URLs logged.')
        except Exception as error:
            missing[host]=set();failed.append(host)
            print('::warning::'+host+' sync unavailable or unconfirmed. All mappings for this host were preserved. Check credentials/API availability. Private details suppressed.')
            if isinstance(error,SyncError):print('::warning::'+str(error)+'. No changes for this host.')
    return missing,failed


def main():
    try:
        repo=os.environ['GITHUB_REPOSITORY'];ref=os.environ['GITHUB_REF_NAME'];token=os.environ['MAPPING_GITHUB_TOKEN']
        if not token or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo):raise ValueError()
        for value in (token,os.getenv('STREAMTAPE_LOGIN'),os.getenv('STREAMTAPE_KEY'),os.getenv('STREAMHG_API_KEY'),os.getenv('ABYSS_API_KEY'),os.getenv('ABYSS_AUTH_TOKEN'),os.getenv('ABYSS_EMAIL'),os.getenv('ABYSS_PASSWORD')):
            if value:mask(value)
        endpoint='https://api.github.com/repos/'+repo+'/contents/mappings.json'
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','Content-Type':'application/json'}
        def request(method='GET',payload=None):
            url=endpoint+'?ref='+urllib.parse.quote(ref,safe='') if method=='GET' else endpoint
            req=urllib.request.Request(url,data=json.dumps(payload).encode() if payload else None,headers=headers,method=method)
            with opener.open(req,timeout=60) as response:return json.loads(response.read(8*1024**2))
        for attempt in range(8):
            try:current=request()
            except urllib.error.HTTPError as error:
                if error.code==404:print('No mappings.json found; no changes.');return 0
                raise
            rows=decrypt_mappings(json.loads(base64.b64decode(current['content'])))
            missing,failed=collect_missing(rows)
            if failed:
                print('::error::Sync incomplete. No mapping changes were saved for any host.')
                return 1
            updated=prune(rows,missing)
            if updated==rows:print('Mapping sync: no changes.');return 1 if failed else 0
            content=json.dumps(encrypt_mappings(updated),indent=2)+'\n'
            payload={'branch':ref,'sha':current['sha'],'message':'Sync confirmed deleted video host mappings','content':base64.b64encode(content.encode()).decode()}
            try:request('PUT',payload)
            except urllib.error.HTTPError as error:
                if error.code==409 and attempt<7:time.sleep(1);continue
                raise
            print('Mapping sync saved encrypted mappings; only twice-confirmed missing host references removed. No IDs or URLs logged.');return 1 if failed else 0
        raise ValueError()
    except Exception:
        print('::error::Mapping sync could not complete. Unconfirmed files are preserved; check API availability, secrets and repository permissions. Raw responses suppressed.')
        return 1

if __name__=='__main__':raise SystemExit(main())
