const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {plan,uploadAll} = require('../.github/actions/parallel-source-upload/index.cjs');

test('all four requests start before any is released; naming and extraction layout preserved',async () => {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'clixarena-artifacts-'));
  try {
    fs.mkdirSync(path.join(dir,'parallel-media'));
    for(let n=0;n<4;n++)fs.writeFileSync(path.join(dir,'parallel-media',`part-0${n}.mkv`),'video');
    const jobs=plan('[0,1,2,3]',dir);
    const started=[],releases=[];
    const client={uploadArtifact(name,files,root,options){
      started.push(name);assert.equal(root,path.join(dir,'parallel-media'));
      assert.deepEqual(options,{compressionLevel:0,retentionDays:1});assert.equal(files.length,1);
      return new Promise(resolve=>releases.push(()=>resolve({id:started.indexOf(name)+1})));
    }};
    const pending=uploadAll(client,jobs,()=>{});
    assert.deepEqual(started,['parallel-source-0','parallel-source-1','parallel-source-2','parallel-source-3']);
    releases.forEach(release=>release());await pending;
  } finally {fs.rmSync(dir,{recursive:true,force:true});}
});

test('partial failure waits for in-flight uploads and never retries or leaks errors',async()=>{
  let release;let calls=0;let settled=false;const logs=[];
  const client={uploadArtifact(){calls++;return calls===1 ? Promise.reject(new Error('SECRET signed-url')) : new Promise(resolve=>{release=()=>resolve({id:2});});}};
  const jobs=[{name:'a',file:'a',root:'.',index:0},{name:'b',file:'b',root:'.',index:1}];
  const pending=uploadAll(client,jobs,line=>logs.push(line));pending.catch(()=>{settled=true;});
  await new Promise(resolve=>setImmediate(resolve));assert.equal(settled,false);
  release();await assert.rejects(pending,/could not be confirmed/);assert.equal(calls,2);
  assert.ok(!logs.join('').includes('SECRET'));assert.ok(!logs.join('').includes('All source artifacts confirmed'));
});

test('bad matrix or missing chunks rejected before any upload',()=>{
  for(const matrix of ['[]','[1]','[0,0]','[0,1,2,3,4]','["0"]','[0,"../secret"]'])assert.throws(()=>plan(matrix,'.'));
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'clixarena-missing-'));
  try {fs.mkdirSync(path.join(dir,'parallel-media'));assert.throws(()=>plan('[0]',dir));}
  finally {fs.rmSync(dir,{recursive:true,force:true});}
});
