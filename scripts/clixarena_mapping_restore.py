"""Restore only StreamHG references removed by a selected sync commit."""
import base64,copy,json,os,re,time,urllib.request,urllib.error,urllib.parse
from clixarena_video import decrypt_mappings,encrypt_mappings,mask,NoRedirect
from clixarena_mapping_sync import host_ids,HOSTS


def identity(row):
    kind=row['type']
    key=('mal_id' if 'mal_id' in row else 'anilist_id') if kind=='anime' else 'tmdb_id'
    return kind,key,row[key],row.get('season',0),row.get('episode',0)


def restore_streamhg(before,after,current):
    after_ids={identity(row):host_ids(row).get('streamhg') for row in after}
    removed={identity(row):row for row in before if host_ids(row).get('streamhg')
             and not after_ids.get(identity(row))}
    result=copy.deepcopy(current)
    indexed={identity(row):row for row in result}
    count=0
    for key,old in removed.items():
        row=indexed.get(key)
        if row is not None and host_ids(row).get('streamhg'):continue
        if row is None:
            row=copy.deepcopy(old)
            for field in HOSTS.values():row.pop(field,None)
            row['providers']={};row['provider']='streamhg'
            result.append(row);indexed[key]=row
        providers=row.setdefault('providers',{})
        fid=host_ids(old)['streamhg']
        providers['streamhg']=copy.deepcopy(old.get('providers',{}).get('streamhg',{'file_id':fid}))
        row['streamhg_id']=fid
        count+=1
    return result,count


def main():
    try:
        repo=os.environ['GITHUB_REPOSITORY'];ref=os.environ['GITHUB_REF_NAME']
        token=os.environ['MAPPING_GITHUB_TOKEN'];sha=os.environ['RESTORE_SYNC_COMMIT'].strip()
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo) or not re.fullmatch('[0-9a-fA-F]{40}',sha):raise ValueError()
        if not token:raise ValueError()
        mask(token)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        def request(path,method='GET',payload=None):
            req=urllib.request.Request('https://api.github.com/repos/'+repo+path,
                data=json.dumps(payload).encode() if payload else None,method=method,
                headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','Content-Type':'application/json'})
            with opener.open(req,timeout=60) as response:
                data=response.read(12*1024**2+1)
                if len(data)>12*1024**2:raise ValueError()
                return json.loads(data)
        commit=request('/commits/'+sha)
        if commit.get('commit',{}).get('message','').split('\n')[0]!='Sync confirmed deleted video host mappings' or len(commit.get('parents',[]))!=1:
            raise ValueError('Select the deletion sync commit')
        parent=commit['parents'][0]['sha']
        def snapshot(revision):
            value=request('/contents/mappings.json?ref='+urllib.parse.quote(revision,safe=''))
            return value,decrypt_mappings(json.loads(base64.b64decode(value['content'])))
        _,before=snapshot(parent);_,after=snapshot(sha)
        for attempt in range(8):
            current,rows=snapshot(ref)
            updated,count=restore_streamhg(before,after,rows)
            if not count:print('No missing StreamHG references to restore. Current mappings preserved.');return 0
            payload={'branch':ref,'sha':current['sha'],'message':'Restore StreamHG references removed by mapping sync',
                     'content':base64.b64encode((json.dumps(encrypt_mappings(updated),indent=2)+'\n').encode()).decode()}
            try:request('/contents/mappings.json','PUT',payload)
            except urllib.error.HTTPError as error:
                if error.code==409 and attempt<7:time.sleep(1);continue
                raise
            print('Restored '+str(count)+' StreamHG reference(s); existing/new uploads, metadata and other providers preserved. No IDs or URLs logged.')
            return 0
        raise ValueError()
    except Exception:
        print('::error::Recovery did not complete. Check the full sync deletion commit SHA, password and repository permissions. Private details suppressed.')
        return 1


if __name__=='__main__':raise SystemExit(main())
