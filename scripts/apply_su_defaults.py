"""Read or apply Map2Model's local SU presentation defaults using a supplied SDK.

No geometry scaling. Input is never overwritten. With --output, sets parallel
top view, mm display, Profiles off and UseSunForAllShading on. Other lighting,
edge visibility, materials, scene pages and application preferences are kept.
"""
import argparse, ctypes as C, hashlib, json, os
from pathlib import Path

class Ref(C.Structure):
    _fields_ = [('ptr', C.c_void_p)]
class Point(C.Structure):
    _fields_ = [('x', C.c_double), ('y', C.c_double), ('z', C.c_double)]

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('input',type=Path)
    ap.add_argument('--sdk',required=True,type=Path,help='Installed SketchUpAPI.dll')
    ap.add_argument('--output',type=Path,help='New SKP path; omit to inspect only')
    ap.add_argument('--report',required=True,type=Path)
    a=ap.parse_args()
    source=a.input.resolve();sdk=a.sdk.resolve();report=a.report.resolve()
    dest=a.output.resolve() if a.output else None
    if report in {source,sdk,dest} or (dest and (dest in {source,sdk} or dest.exists())):
        raise ValueError('Output/report must not overwrite input, SDK, or an existing SKP')
    if not source.is_file() or not sdk.is_file():raise FileNotFoundError('Input or SDK missing')
    dll_dir=os.add_dll_directory(str(sdk.parent));lib=C.CDLL(str(sdk))
    P=C.POINTER;R=Ref;T=C.c_char_p;B=C.c_bool;I=C.c_int;D=C.c_double
    signatures={
        'SUInitialize':[], 'SUTerminate':[], 'SUModelCreateFromFileWithStatus':[P(R),T,P(I)],
        'SUModelRelease':[P(R)],'SUModelSaveToFile':[R,T],
        'SUModelGetRenderingOptions':[R,P(R)],'SUModelGetShadowInfo':[R,P(R)],
        'SURenderingOptionsGetValue':[R,T,P(R)],'SURenderingOptionsSetValue':[R,T,R],
        'SUShadowInfoGetValue':[R,T,P(R)],'SUShadowInfoSetValue':[R,T,R],
        'SUModelGetOptionsManager':[R,P(R)],'SUOptionsManagerGetOptionsProviderByName':[R,T,P(R)],
        'SUOptionsProviderGetValue':[R,T,P(R)],'SUOptionsProviderSetValue':[R,T,R],
        'SUTypedValueCreate':[P(R)],'SUTypedValueRelease':[P(R)],
        'SUTypedValueGetBool':[R,P(B)],'SUTypedValueSetBool':[R,B],
        'SUTypedValueGetInt32':[R,P(I)],'SUTypedValueSetInt32':[R,I],
        'SUModelGetCamera':[R,P(R)],'SUCameraGetOrientation':[R,P(Point),P(Point),P(Point)],
        'SUCameraSetOrientation':[R,P(Point),P(Point),P(Point)],
        'SUCameraGetPerspective':[R,P(B)],'SUCameraSetPerspective':[R,B],
        'SUCameraGetOrthographicFrustumHeight':[R,P(D)],
        'SUCameraSetOrthographicFrustumHeight':[R,D],
    }
    funcs={}
    for n,args in signatures.items():
        f=getattr(lib,n);f.argtypes=args;f.restype=None if n in ('SUInitialize','SUTerminate') else I;funcs[n]=f
    def call(n,*args):
        result=funcs[n](*args)
        if result not in (None,0):raise RuntimeError(f'{n} returned {result}')
    def ref(n,*args):
        result=R();call(n,*args,C.byref(result));return result
    def load(path):
        model=R();status=I();call('SUModelCreateFromFileWithStatus',C.byref(model),str(path).encode('utf8'),C.byref(status))
        if status.value!=0:raise RuntimeError(f'Model load status {status.value}; do not silently apply to a partially loaded file')
        return model
    def option(obj,api,key,kind,value=None):
        v=ref('SUTypedValueCreate')
        try:
            if value is None:
                call(api+'GetValue',obj,key.encode(),C.byref(v));out=B() if kind=='Bool' else I()
                call('SUTypedValueGet'+kind,v,C.byref(out));return out.value
            call('SUTypedValueSet'+kind,v,value);call(api+'SetValue',obj,key.encode(),v)
        finally:call('SUTypedValueRelease',C.byref(v))
    def providers(model):
        rendering=ref('SUModelGetRenderingOptions',model);shadow=ref('SUModelGetShadowInfo',model)
        manager=ref('SUModelGetOptionsManager',model);units=ref('SUOptionsManagerGetOptionsProviderByName',manager,b'UnitsOptions')
        return rendering,shadow,units
    def snapshot(model):
        rendering,shadow,units=providers(model);camera=ref('SUModelGetCamera',model)
        eye=Point();target=Point();up=Point();call('SUCameraGetOrientation',camera,C.byref(eye),C.byref(target),C.byref(up))
        perspective=B();call('SUCameraGetPerspective',camera,C.byref(perspective))
        return dict(
            rendering={k:option(rendering,'SURenderingOptions',k,t) for k,t in [('DrawSilhouettes','Bool'),('DrawProfilesOnly','Bool'),('EdgeDisplayMode','Int32')]},
            shadow={k:option(shadow,'SUShadowInfo',k,t) for k,t in [('UseSunForAllShading','Bool'),('DisplayShadows','Bool'),('Light','Int32'),('Dark','Int32')]},
            units={k:option(units,'SUOptionsProvider',k,'Int32') for k in ('LengthFormat','LengthUnit')},
            camera=dict(perspective=perspective.value,eye=[eye.x,eye.y,eye.z],target=[target.x,target.y,target.z],up=[up.x,up.y,up.z]))
    call('SUInitialize');model=R()
    try:
        model=load(source);before=snapshot(model)
        result=dict(input_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),before=before,scope='Saved-file option readback; not GUI inspection')
        if dest:
            rendering,shadow,units=providers(model)
            option(rendering,'SURenderingOptions','DrawSilhouettes','Bool',False)
            option(shadow,'SUShadowInfo','UseSunForAllShading','Bool',True)
            option(units,'SUOptionsProvider','LengthFormat','Int32',0)
            option(units,'SUOptionsProvider','LengthUnit','Int32',2)
            camera=ref('SUModelGetCamera',model);c=before['camera'];target=Point(*c['target'])
            distance=max(sum((x-y)**2 for x,y in zip(c['eye'],c['target']))**.5,1.0)
            eye=Point(target.x,target.y,target.z+distance);up=Point(0,1,0)
            call('SUCameraSetOrientation',camera,C.byref(eye),C.byref(target),C.byref(up))
            call('SUCameraSetPerspective',camera,False)
            if c['perspective']:call('SUCameraSetOrthographicFrustumHeight',camera,distance)
            dest.parent.mkdir(parents=True,exist_ok=True);call('SUModelSaveToFile',model,str(dest).encode('utf8'))
            call('SUModelRelease',C.byref(model));model=load(dest);after=snapshot(model)
            assert after['rendering']['DrawSilhouettes'] is False
            assert after['shadow']['UseSunForAllShading'] is True
            assert after['units']=={'LengthFormat':0,'LengthUnit':2}
            assert after['camera']['perspective'] is False
            assert after['camera']['eye'][:2]==after['camera']['target'][:2]
            assert after['camera']['up']==[0,1,0]
            for key in ('DisplayShadows','Light','Dark'):assert before['shadow'][key]==after['shadow'][key]
            for key in ('DrawProfilesOnly','EdgeDisplayMode'):assert before['rendering'][key]==after['rendering'][key]
            result.update(after=after,output_sha256=hashlib.sha256(dest.read_bytes()).hexdigest(),status='saved_options_verified')
        report.parent.mkdir(parents=True,exist_ok=True);report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:
        if model.ptr:call('SUModelRelease',C.byref(model))
        call('SUTerminate');dll_dir.close()

if __name__=='__main__':main()
