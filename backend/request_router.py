from fastapi import FastAPI, HTTPException, Request
import gc
import time

# Simple field profile cache — 5 min TTL
_profile_cache = {}
_PROFILE_TTL = 300  # 5 minutes
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from backend.bootstrap import Bootstrap
from extractors.weather_extractor import get_weather_data
from extractors.grass_model import estimate_grass_cover
from extractors.nitrogen_planner import calculate_n_window
from extractors.irrigation_planner import calculate_irrigation
from extractors.slurry_planner import calculate_slurry_window

app = FastAPI(title="Cube Earth API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

service = Bootstrap().build()


class ProfileRequest(BaseModel):
    lat:  float
    lng:  float
    year: int = 2026
    parcel_override: dict = None


@app.get("/zone_xyz/{z}/{x}/{y}.png")
async def zone_xyz(z: int, x: int, y: int):
    """XYZ tile endpoint for variability zones — compatible with L.tileLayer."""
    import httpx, io, numpy as np, math
    from PIL import Image
    from config.settings import settings
    from fastapi.responses import Response

    # Convert XYZ to bbox
    def tile_to_bbox(x, y, z):
        n = 2**z
        lon_min = x/n*360 - 180
        lon_max = (x+1)/n*360 - 180
        lat_max = math.degrees(math.atan(math.sinh(math.pi*(1-2*y/n))))
        lat_min = math.degrees(math.atan(math.sinh(math.pi*(1-2*(y+1)/n))))
        return lon_min, lat_min, lon_max, lat_max

    minlng, minlat, maxlng, maxlat = tile_to_bbox(x, y, z)

    # Only render for Ireland bbox
    if maxlng < -11 or minlng > -5 or maxlat < 51 or minlat > 56:
        # Return transparent tile
        img = Image.new('RGBA', (256,256), (0,0,0,0))
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return Response(content=buf.getvalue(), media_type='image/png')

    evalscript = """//VERSION=3
function setup(){return{input:[{bands:["B04","B08"],units:"REFLECTANCE"}],output:{bands:1,sampleType:"UINT8"}}}
function evaluatePixel(s){var ndvi=(s.B08-s.B04)/(s.B08+s.B04+0.0001);return[Math.round((ndvi+1)*127.5)];}"""

    now = __import__('datetime').datetime.utcnow()
    t_end = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    t_start = (now - __import__('datetime').timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            token_r = await client.post(
                "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token",
                data={"grant_type":"client_credentials","client_id":settings.CDSE_CLIENT_ID,"client_secret":settings.CDSE_CLIENT_SECRET}
            )
            token = token_r.json().get("access_token")
            r = await client.post(
                "https://sh.dataspace.copernicus.eu/api/v1/process",
                headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},
                json={
                    "input":{
                        "bounds":{"bbox":[minlng,minlat,maxlng,maxlat],"properties":{"crs":"http://www.opengis.net/def/crs/EPSG/0/4326"}},
                        "data":[{"type":"sentinel-2-l2a","dataFilter":{"timeRange":{"from":t_start,"to":t_end},"maxCloudCoverage":80,"mosaickingOrder":"leastCC"}}]
                    },
                    "evalscript":evalscript,
                    "output":{"width":256,"height":256,"responses":[{"identifier":"default","format":{"type":"image/png"}}]}
                }
            )
        img = Image.open(io.BytesIO(r.content)).convert("L")
        arr = np.array(img).astype(float)
        ndvi = (arr/127.5)-1.0
        veg_mask = ndvi > 0.1
        veg_pixels = ndvi[veg_mask]
        out = np.zeros((256,256,4), dtype=np.uint8)
        if len(veg_pixels) > 10:
            p_low = np.percentile(veg_pixels, 25)
            p_high = np.percentile(veg_pixels, 75)
            out[veg_mask & (ndvi>=p_high)] = [22,163,74,180]
            out[veg_mask & (ndvi>=p_low) & (ndvi<p_high)] = [217,119,6,180]
            out[veg_mask & (ndvi<p_low)] = [220,38,38,180]
        zone_img = Image.fromarray(out,'RGBA')
        buf = io.BytesIO()
        zone_img.save(buf, format='PNG')
        return Response(content=buf.getvalue(), media_type='image/png')
    except Exception:
        img = Image.new('RGBA',(256,256),(0,0,0,0))
        buf = io.BytesIO()
        img.save(buf,format='PNG')
        return Response(content=buf.getvalue(),media_type='image/png')

@app.get("/zone_tile")
async def zone_tile(
    minlng: float = -9.5,
    minlat: float = 52.0,
    maxlng: float = -9.4,
    maxlat: float = 52.1,
    time: str = "2026-04-01/2026-06-03"
):
    """Returns a relative variability zone PNG — zones based on field's own NDVI distribution."""
    import httpx, io, numpy as np
    from PIL import Image
    from config.settings import settings
    from fastapi.responses import Response

    # Get NDVI PNG from CDSE
    evalscript = """//VERSION=3
function setup(){
  return{input:[{bands:["B04","B08"],units:"REFLECTANCE"}],output:{bands:1,sampleType:"UINT8"}}
}
function evaluatePixel(s){
  var ndvi=(s.B08-s.B04)/(s.B08+s.B04+0.0001);
  return[Math.round((ndvi+1)*127.5)];
}"""

    t0, t1 = time.split("/")
    async with httpx.AsyncClient(timeout=30) as client:
        token_r = await client.post(
            "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token",
            data={"grant_type":"client_credentials","client_id":settings.CDSE_CLIENT_ID,"client_secret":settings.CDSE_CLIENT_SECRET}
        )
        token = token_r.json().get("access_token")
        r = await client.post(
            "https://sh.dataspace.copernicus.eu/api/v1/process",
            headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},
            json={
                "input":{
                    "bounds":{"bbox":[minlng,minlat,maxlng,maxlat],"properties":{"crs":"http://www.opengis.net/def/crs/EPSG/0/4326"}},
                    "data":[{"type":"sentinel-2-l2a","dataFilter":{"timeRange":{"from":f"{t0}T00:00:00Z","to":f"{t1}T23:59:59Z"},"maxCloudCoverage":80,"mosaickingOrder":"leastCC"}}]
                },
                "evalscript": evalscript,
                "output":{"width":256,"height":256,"responses":[{"identifier":"default","format":{"type":"image/png"}}]}
            }
        )

    # Decode NDVI
    img = Image.open(io.BytesIO(r.content)).convert("L")
    arr = np.array(img).astype(float)
    ndvi = (arr / 127.5) - 1.0

    # Mask non-vegetated pixels
    veg_mask = ndvi > 0.1

    # Calculate field-relative percentile breaks
    veg_pixels = ndvi[veg_mask]
    if len(veg_pixels) < 10:
        return Response(content=r.content, media_type="image/png")

    p_low  = np.percentile(veg_pixels, 25)
    p_high = np.percentile(veg_pixels, 75)

    # Create RGB zone image
    out = np.zeros((256, 256, 4), dtype=np.uint8)

    # High zone — green
    high = veg_mask & (ndvi >= p_high)
    out[high] = [22, 163, 74, 200]

    # Medium zone — amber
    med = veg_mask & (ndvi >= p_low) & (ndvi < p_high)
    out[med] = [217, 119, 6, 200]

    # Low zone — red
    low = veg_mask & (ndvi < p_low)
    out[low] = [220, 38, 38, 200]

    # Non-veg — transparent
    out[~veg_mask] = [0, 0, 0, 0]

    zone_img = Image.fromarray(out, 'RGBA')
    buf = io.BytesIO()
    zone_img.save(buf, format="PNG")
    buf.seek(0)
    return Response(content=buf.read(), media_type="image/png")

@app.get("/test_historical")
async def test_historical(lat: float = 52.10, lng: float = -9.40):
    from extractors.cdse_historical_extractor import CDSEHistoricalExtractor
    e = CDSEHistoricalExtractor()
    r = await e.extract(lat, lng, years=5)
    return r

@app.get("/test_trend")
async def test_trend(lat: float = 52.10, lng: float = -9.40):
    from extractors.cdse_trend_extractor import CDSETrendExtractor
    e = CDSETrendExtractor()
    r = await e.extract(lat, lng)
    return r

@app.get("/test_cdse")
async def test_cdse(lat: float = 52.05, lng: float = -9.35):
    from extractors.cdse_optical_extractor import CDSEOpticalExtractor
    e = CDSEOpticalExtractor()
    r = await e.extract(lat, lng)
    return r

@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    return {"status": "ok", "service": "cube-earth-india"}


@app.get("/parcels_in_bbox")
async def parcels_in_bbox(minlng: float, minlat: float, maxlng: float, maxlat: float):
    import httpx
    url = f"https://geoapi.opendata.agriculture.gov.ie/shps/collections/anonymous-lpis-data-for-2024_2024-lpis-data/items?bbox={minlng},{minlat},{maxlng},{maxlat}&f=json&limit=50"
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url)
        return r.json()

@app.get("/parcel_at_point")
async def parcel_at_point(lat: float, lng: float):
    from parcel.dafm_api import get_parcel_at_point
    result = await get_parcel_at_point(lat, lng)
    if not result:
        return {"features": [], "error": "No parcel found"}
    return {"features": [{"properties": result}], "matched": True}

@app.post("/field_profile")
async def field_profile(req: ProfileRequest):
    try:
        # Check cache
        cache_key = f"{round(req.lat,4)}_{round(req.lng,4)}"
        now = time.time()
        if cache_key in _profile_cache:
            ts, cached = _profile_cache[cache_key]
            if now - ts < _PROFILE_TTL:
                return cached

        result = await service.build_profile(req.lat, req.lng, req.year, parcel_override=req.parcel_override)
        # Weather + grass model
        grass = None
        weather = None
        try:
            weather = get_weather_data(req.lat, req.lng)
            ndvi = result.get('vegetation', {}).get('ndvi')
            rvi = result.get('sar', {}).get('rvi')
            smap = result.get('soil_moisture', {}).get('smap', {}).get('sm_surface_m3')
            grass = estimate_grass_cover(ndvi, rvi, smap, weather)
            result['weather'] = weather
            result['grass_model'] = grass
        except Exception as e:
            result['weather'] = {'available': False, 'error': str(e)}
            result['grass_model'] = {'available': False}
        # Nitrogen planner
        cover_kg = grass.get("kg_dm_ha") if isinstance(grass, dict) else None
        smap_val = result.get('soil_moisture', {}).get('smap', {}).get('sm_surface_m3')
        n_plan = calculate_n_window(weather, smap_val, cover_kg, req.parcel_override.get('crop') if req.parcel_override else None)

        # Slurry planner
        slurry = calculate_slurry_window(weather, smap_val)

        # Irrigation planner
        crop_name = req.parcel_override.get('crop','Grapes') if req.parcel_override else 'Grapes'
        area_ha = req.parcel_override.get('claim_area', 1.0) if req.parcel_override else 1.0
        irrigation = calculate_irrigation(weather, smap_val, crop_name, area_ha)

        response = {"success": True,
                "nitrogen": n_plan,
                "slurry": slurry,
                "irrigation": irrigation,
                **result}
        # Cache result
        _profile_cache[cache_key] = (time.time(), response)
        if len(_profile_cache) > 100:
            oldest = min(_profile_cache, key=lambda k: _profile_cache[k][0])
            del _profile_cache[oldest]
        gc.collect()
        return response
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/wms_proxy")
async def wms_proxy(
    request: Request,
    z: int = 14, x: int = 7764, y: int = 5404,
    layer: str = "TRUE_COLOR",
    time: str = ""
):
    """Proxy Sentinel Hub WMS tiles with OAuth token."""
    import httpx, datetime as dt
    from config.settings import settings
    from fastapi.responses import Response

    if not time:
        now = dt.datetime.utcnow()
        time = f"{(now - dt.timedelta(days=60)).strftime('%Y-%m-%d')}/{now.strftime('%Y-%m-%d')}"

    # Get token
    async with httpx.AsyncClient(timeout=15) as client:
        tr = await client.post(
            "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token",
            data={"grant_type":"client_credentials","client_id":settings.CDSE_CLIENT_ID,"client_secret":settings.CDSE_CLIENT_SECRET}
        )
        token = tr.json().get("access_token")

        # Convert XYZ to bbox
        import math
        n = 2**z
        lon_min = x/n*360 - 180
        lon_max = (x+1)/n*360 - 180
        lat_max = math.degrees(math.atan(math.sinh(math.pi*(1-2*y/n))))
        lat_min = math.degrees(math.atan(math.sinh(math.pi*(1-2*(y+1)/n))))

        evalscript = """//VERSION=3
function setup(){return{input:[{bands:["B02","B03","B04"],units:"REFLECTANCE"}],output:{bands:3,sampleType:"UINT8"}}}
function evaluatePixel(s){
  // Sentinel Hub viewer-quality enhancement
  var r=s.B04, g=s.B03, b=s.B02;
  // Linear stretch + gamma matching Sentinel Hub viewer
  r=Math.min(1,(r-0.0)*3.5); g=Math.min(1,(g-0.0)*3.5); b=Math.min(1,(b-0.0)*3.5);
  r=Math.pow(Math.max(0,r),0.75); g=Math.pow(Math.max(0,g),0.75); b=Math.pow(Math.max(0,b),0.75);
  return[Math.round(r*255),Math.round(g*255),Math.round(b*255)];
}"""
        t0,t1 = time.split("/") if "/" in time else (time,time)
        r = await client.post(
            "https://sh.dataspace.copernicus.eu/api/v1/process",
            headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},
            json={
                "input":{
                    "bounds":{"bbox":[lon_min,lat_min,lon_max,lat_max],"properties":{"crs":"http://www.opengis.net/def/crs/EPSG/0/4326"}},
                    "data":[{"type":"sentinel-2-l2a","dataFilter":{"timeRange":{"from":f"{t0}T00:00:00Z","to":f"{t1}T23:59:59Z"},"maxCloudCoverage":30,"mosaickingOrder":"leastCC"}}]
                },
                "evalscript":evalscript,
                "output":{"width":1024,"height":1024,"responses":[{"identifier":"default","format":{"type":"image/jpeg","quality":95}}]}
            }
        )
    return Response(content=r.content, media_type="image/jpeg")

@app.get("/wms_token")
async def wms_token():
    """Get short-lived token for Sentinel Hub WMS tiles."""
    import httpx
    from config.settings import settings
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": settings.CDSE_CLIENT_ID,
                "client_secret": settings.CDSE_CLIENT_SECRET,
            }
        )
        data = r.json()
        token = data.get("access_token")
        return {
            "token": token,
            "wms_url": f"https://sh.dataspace.copernicus.eu/ogc/wms/TOKEN?token={token}"
        }

INSTANCE_ID = "bfba5ae1-06b9-41d1-b2eb-771c247f9ac9"

@app.get("/wms_tile")
async def wms_tile(
    minlng: float = -9.5,
    minlat: float = 52.0,
    maxlng: float = -9.4,
    maxlat: float = 52.1,
    time: str = "",
    style: str = "rgb"
):
    """Sentinel-2 tile via Process API. style=rgb|ndvi"""
    import datetime as dt
    if not time:
        now = dt.datetime.utcnow()
        time = f"{(now - dt.timedelta(days=60)).strftime('%Y-%m-%d')}/{now.strftime('%Y-%m-%d')}"
    import httpx
    from config.settings import settings
    from fastapi.responses import Response

    if style == "ndvi":
        evalscript = """//VERSION=3
function setup(){return{input:["B04","B08"],output:{bands:3,sampleType:"UINT8"}}}
function evaluatePixel(s){
  var ndvi=(s.B08-s.B04)/(s.B08+s.B04+0.0001);
  if(ndvi>0.75) return [0,200,50];
  if(ndvi>0.60) return [50,180,0];
  if(ndvi>0.45) return [150,210,0];
  if(ndvi>0.30) return [230,200,0];
  if(ndvi>0.15) return [230,120,0];
  return [200,30,0];
}"""
    elif style == "zones":
        evalscript = """//VERSION=3
function setup(){return{input:["B04","B08"],output:{bands:3,sampleType:"UINT8"}}}
function evaluatePixel(s){
  var ndvi=(s.B08-s.B04)/(s.B08+s.B04+0.0001);
  // Hard zone boundaries: green/amber/red
  if(ndvi>0.65) return [0,180,0];
  if(ndvi>0.45) return [200,160,0];
  return [200,0,0];
}"""
    else:
        evalscript = """//VERSION=3
function setup(){
  return{
    input:[{bands:["B02","B03","B04"],units:"REFLECTANCE"}],
    output:{bands:3,sampleType:"UINT8"}
  }
}
function evaluatePixel(s){
  // Highlight Optimized Natural Color
  var r=s.B04, g=s.B03, b=s.B02;
  // Gain and gamma correction
  var gain=3.5, gamma=0.85;
  r=Math.pow(Math.min(1,r*gain),gamma);
  g=Math.pow(Math.min(1,g*gain),gamma);
  b=Math.pow(Math.min(1,b*gain),gamma);
  // Contrast stretch
  var min=0.05, max=0.95;
  r=(r-min)/(max-min);
  g=(g-min)/(max-min);
  b=(b-min)/(max-min);
  return[
    Math.round(Math.max(0,Math.min(1,r))*255),
    Math.round(Math.max(0,Math.min(1,g))*255),
    Math.round(Math.max(0,Math.min(1,b))*255)
  ];
}"""

    async with httpx.AsyncClient(timeout=30) as client:
        token_r = await client.post(
            "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token",
            data={"grant_type":"client_credentials","client_id":settings.CDSE_CLIENT_ID,"client_secret":settings.CDSE_CLIENT_SECRET}
        )
        token = token_r.json().get("access_token")
        t0, t1 = time.split("/")
        process_r = await client.post(
            "https://sh.dataspace.copernicus.eu/api/v1/process",
            headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},
            json={
                "input":{
                    "bounds":{"bbox":[minlng,minlat,maxlng,maxlat],"properties":{"crs":"http://www.opengis.net/def/crs/EPSG/0/4326"}},
                    "data":[{"type":"sentinel-2-l2a","dataFilter":{"timeRange":{"from":f"{t0}T00:00:00Z","to":f"{t1}T23:59:59Z"},"maxCloudCoverage":80,"mosaickingOrder":"leastCC"}}]
                },
                "evalscript":evalscript,
                "output":{"width":2048,"height":2048,"responses":[{"identifier":"default","format":{"type":"image/jpeg","quality":90}}]}
            }
        )
        return Response(content=process_r.content,media_type="image/jpeg")


# wms_proxy added Wed Jun  3 14:35:37 UTC 2026
# Force redeploy Wed Jun  3 20:37:53 UTC 2026
# redeploy Fri Jun  5 04:44:01 UTC 2026

@app.post("/analyse_photo")
async def analyse_photo(request: Request):
    import anthropic, os, json
    body = await request.json()
    image_data = body.get("image_data")
    if not image_data:
        return {"error": "no image"}
    try:
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_data}},
                    {"type": "text", "text": "You are an expert Irish grassland agronomist. Analyse this field photo. Respond ONLY in JSON: {\"detected\":\"main issue in 2-4 words\",\"confidence\":82,\"species\":[\"species1\"],\"recommendation\":\"one clear action sentence\",\"issue_type\":\"weeds|compaction|thin_sward|overgrazing|drainage|healthy|other\"}"}
                ]
            }]
        )
        text = message.content[0].text
        return json.loads(text.replace("```json","").replace("```","").strip())
    except Exception as e:
        return {"error": str(e)}

# Supabase client
import os
from supabase import create_client

SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ.get('SUPABASE_KEY')
supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

@app.post("/save_farm")
async def save_farm(request: Request):
    """Save farm and fields to Supabase"""
    try:
        body = await request.json()
        session_id = body.get('session_id')
        farm_data = body.get('farm_data', {})
        fields = body.get('fields', [])

        if not supabase:
            return {"success": False, "error": "Database not configured"}

        # Save farm
        farm_result = supabase.table('farms').insert({
            'user_id': session_id,
            'farm_name': farm_data.get('name', 'My Farm'),
            'county': farm_data.get('county', ''),
            'enterprise_type': farm_data.get('type', ''),
            'acres_scheme': farm_data.get('acres', 'no'),
        }).execute()

        farm_id = farm_result.data[0]['id']

        # Save fields
        field_rows = []
        for f in fields:
            field_rows.append({
                'farm_id': farm_id,
                'user_id': session_id,
                'crop': f.get('crop'),
                'area': f.get('area'),
                'olr': f.get('olr'),
                'lat': f.get('lat'),
                'lng': f.get('lng'),
                'eco': f.get('eco', False),
                'biss': f.get('biss', False),
                'par_lab': f.get('id'),
                'geometry': f.get('geometry'),
                'ndvi': f.get('ndvi'),
                'cover': f.get('cover'),
                'status': f.get('status'),
                'action': f.get('action'),
                'profile_data': f.get('profileData'),
            })

        if field_rows:
            supabase.table('cube_fields').insert(field_rows).execute()

        return {"success": True, "farm_id": farm_id}

    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/load_farm")
async def load_farm(session_id: str):
    """Load farm and fields from Supabase"""
    try:
        if not supabase:
            return {"success": False, "error": "Database not configured"}

        # Get ALL farms for this session
        farm_result = supabase.table('farms')\
            .select('*')\
            .eq('user_id', session_id)\
            .order('created_at', desc=True)\
            .execute()

        if not farm_result.data:
            return {"success": False, "error": "No farm found"}

        # Get fields for all farms
        all_farms = []
        for farm in farm_result.data:
            fields_result = supabase.table('cube_fields')\
                .select('*')\
                .eq('farm_id', farm['id'])\
                .execute()
            all_farms.append({
                "farm": farm,
                "fields": fields_result.data
            })

        return {
            "success": True,
            "farms": all_farms,
            "farm": farm_result.data[0],
            "fields": all_farms[0]["fields"] if all_farms else []
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/save_plate_meter")
async def save_plate_meter(request: Request):
    """Save plate meter reading"""
    try:
        body = await request.json()
        if not supabase:
            return {"success": False, "error": "Database not configured"}

        result = supabase.table('plate_meter_readings').insert({
            'field_id': body.get('field_id'),
            'user_id': body.get('session_id'),
            'reading_kg_dm_ha': body.get('reading'),
            'satellite_estimate': body.get('satellite_estimate'),
        }).execute()

        return {"success": True, "id": result.data[0]['id']}

    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/save_inspection")
async def save_inspection(request: Request):
    """Save inspection to Supabase"""
    try:
        body = await request.json()
        if not supabase:
            return {"success": False, "error": "Database not configured"}

        result = supabase.table('inspections').insert({
            'field_id': body.get('field_id') or None,
            'user_id': body.get('session_id') or 'anonymous',
            'zone': body.get('zone'),
            'ndvi': body.get('ndvi'),
            'field_mean': body.get('field_mean'),
            'issues': body.get('issues', []),
            'severity': body.get('severity'),
            'notes': body.get('notes'),
            'date': body.get('date'),
            'lat': body.get('lat'),
            'lng': body.get('lng'),
        }).execute()

        return {"success": True, "id": result.data[0]['id']}

    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/save_verification")
async def save_verification(request: Request):
    """Save verification check"""
    try:
        body = await request.json()
        if not supabase:
            return {"success": False, "error": "Database not configured"}

        result = supabase.table('verifications').insert({
            'inspection_id': body.get('inspection_id'),
            'field_id': body.get('field_id'),
            'user_id': body.get('session_id'),
            'issue': body.get('issue'),
            'target_ndvi': body.get('target_ndvi'),
            'verify_by_date': body.get('verify_by_date'),
            'status': 'pending',
        }).execute()

        return {"success": True, "id": result.data[0]['id']}

    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/upload_photo")
async def upload_photo(request: Request):
    """Upload inspection photo to Supabase Storage"""
    try:
        body = await request.json()
        if not supabase:
            return {"success": False, "error": "Database not configured"}

        import base64, uuid
        photo_data = body.get('photo_base64', '')
        session_id = body.get('session_id', 'anonymous')
        inspection_id = body.get('inspection_id')
        field_id = body.get('field_id')

        # Strip data URL prefix if present
        if ',' in photo_data:
            photo_data = photo_data.split(',')[1]

        # Decode base64
        photo_bytes = base64.b64decode(photo_data)

        # Generate unique filename
        filename = f"{session_id}/{uuid.uuid4()}.jpg"

        # Upload to Supabase Storage
        result = supabase.storage.from_('inspection-photos').upload(
            filename,
            photo_bytes,
            {"content-type": "image/jpeg"}
        )

        # Get public URL
        url_result = supabase.storage.from_('inspection-photos').get_public_url(filename)

        return {
            "success": True,
            "filename": filename,
            "url": url_result
        }

    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/plate_meter_history")
async def plate_meter_history(field_id: str = None, session_id: str = None):
    """Get plate meter reading history for a field"""
    try:
        if not supabase:
            return {"success": False, "error": "Database not configured"}

        query = supabase.table('plate_meter_readings').select('*')

        if field_id and field_id.strip():
            query = query.eq('field_id', field_id)
        elif session_id:
            query = query.eq('user_id', session_id)

        result = query.order('created_at', desc=True).limit(20).execute()

        return {"success": True, "readings": result.data}

    except Exception as e:
        return {"success": False, "error": str(e)}

