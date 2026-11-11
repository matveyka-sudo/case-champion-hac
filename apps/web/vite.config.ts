import react from '@vitejs/plugin-react';
import {defineConfig,loadEnv} from 'vite';
export default defineConfig(({mode})=>{
 const env=loadEnv(mode,process.cwd(),'');
 const target=env.BACKEND_PROXY_TARGET;
 return {plugins:[react()],server:target?{proxy:{'/api/v1':{target,changeOrigin:false},'/health':{target,changeOrigin:false}}}:undefined};
});
