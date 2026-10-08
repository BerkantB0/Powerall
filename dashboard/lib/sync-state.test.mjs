import { test } from 'node:test';
import assert from 'node:assert/strict';
import { refreshState } from './sync-state.ts';
const failed={status:'failed',started:1000};
test('a deferred failure allows manual retry after the short server cooldown',()=>{
 const state=refreshState({run:failed,queue:{state:'queued',available_at:90000,retry:true}},1061);
 assert.equal(state.busy,false);assert.equal(state.retry,true);assert.equal(state.retryWait,0);
 assert.equal(refreshState({run:failed,queue:{state:'queued',available_at:2000,retry:true}},1030).retryWait,30);
});
test('waiting fresh jobs and active workers stay busy; explicit metadata beats old failure',()=>{
 for(const state of ['queued','running']) assert.equal(refreshState({run:failed,queue:{state,available_at:1000,retry:false}},1061).busy,true);
 assert.equal(refreshState({run:{status:'running',started:1000},queue:null}).busy,true);
});
test('old API statuses remain compatible and no queue means no scheduled retry',()=>{
 assert.equal(refreshState({run:failed,queue:{state:'queued',available_at:2000}},1061).retry,true);
 assert.equal(refreshState({run:failed,queue:null}).busy,false);
 assert.equal(refreshState(undefined).retry,false);
});
