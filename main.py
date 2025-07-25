from fastapi import FastAPI, Query
from typing import List
import httpx
import datetime

from fastapi.responses import HTMLResponse, JSONResponse, Response

CAMPGROUND_LOOKUP = {
    "232369": "Camp Dick",
    "232462": "Glacier Basin",
    "232281": "Olive Ridge",
    "232368": "Peaceful Valley",
    "232280": "Kelly Dahl",
    "232282": "Pawnee Campground",
    "231862": "Stillwater Campground",
    "231861": "Green Ridge Campground",
    "232463": "Moraine Park Campground",
    "233187": "Aspenglen Campground",
    "260552": "Timber Creek Campground",
    "231860": "Arapaho Bay Campground"
}

RECREATION_API_URL = "https://www.recreation.gov/api/camps/availability/campground/{campground_id}/month"

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
def root():
    campground_links = []
    for cid, name in CAMPGROUND_LOOKUP.items():
        summary = "(loading...)"
        campground_links.append(
            f'<li><a href="https://www.recreation.gov/camping/campgrounds/{cid}" target="_blank">{name}</a> - <span id="summary-{cid}">{summary}</span></li>'
        )
    campground_list_html = "".join(campground_links)
    # Escape curly braces for JS template literals inside Python f-string
    return f"""
    <html>
    <head>
        <title>Campground Availability</title>
    </head>
    <body>
        <h1>Campground Availability Checker</h1>
        <form action='/availability' method='get'>
            <label for='startDate'>Start Date:</label>
            <input type='date' id='startDate' name='startDate' required>
            <label for='endDate'>End Date:</label>
            <input type='date' id='endDate' name='endDate' required>
            <br><br>
            <input type='submit' value='Check Availability'>
        </form>
        <h2>Campgrounds</h2>
        <ul>{campground_list_html}</ul>
        <script>
        async function fetchSummaries() {{
            const params = new URLSearchParams(window.location.search);
            if (!params.has('startDate') || !params.has('endDate')) return;
            const response = await fetch(`/availability?${{params.toString()}}`, {{
                headers: {{'Accept': 'application/json'}}
            }});
            const data = await response.json();
            for (const [cid, info] of Object.entries(data)) {{
                if (!info || !info.campground_name) continue;
                const fully = info.fully_available_sites?.length || 0;
                const partially = info.partially_available_sites?.length || 0;
                const summary = `${{fully}} full, ${{partially}} partial`;
                const el = document.getElementById(`summary-${{cid}}`);
                if (el) el.innerText = summary;
            }}
        }}
        fetchSummaries();
        </script>
    </body>
    </html>
    """

@app.get("/availability")
def get_availability(
    campgroundName: List[str] = Query(...),
    startDate: str = Query("2025-08-01", alias="startDate"),
    endDate: str = Query("2025-08-03", alias="endDate"),
    accept: str = Query(None, alias="accept")
) -> Response:
    def daterange(start_date, end_date):
        for n in range((end_date - start_date).days + 1):
            yield (start_date + datetime.timedelta(n)).strftime("%Y-%m-%dT00:00:00Z")

    start = datetime.datetime.strptime(startDate, "%Y-%m-%d")
    end = datetime.datetime.strptime(endDate, "%Y-%m-%d")
    target_dates = list(daterange(start, end))

    results = {}
    all_sites = {}

    name_to_id = {v.lower(): k for k, v in CAMPGROUND_LOOKUP.items()}
    for name in campgroundName:
        campgroundId = name_to_id.get(name.lower())
        if not campgroundId:
            results[name] = {"error": "Unknown campground name"}
            continue
        months = sorted(set(datetime.datetime.strptime(d.split("T")[0], "%Y-%m-%d").replace(day=1) for d in target_dates))
        all_avail_data = {}

        for month_date in months:
            month = month_date.strftime("%Y-%m-%dT00:00:00.000Z")
            avail_url = RECREATION_API_URL.format(campground_id=campgroundId)
            params = {"start_date": month}

            try:
                with httpx.Client() as client:
                    avail_resp = client.get(avail_url, params=params)
                    avail_resp.raise_for_status()
                    month_data = avail_resp.json()
                    for site_id, site_info in month_data.get("campsites", {}).items():
                        if site_id not in all_avail_data:
                            all_avail_data[site_id] = site_info
                        else:
                            all_avail_data[site_id]["availabilities"].update(site_info.get("availabilities", {}))
            except Exception as e:
                results[campgroundId] = {"error": f"Failed to fetch month {month}: {str(e)}"}
                continue

        campground_name = CAMPGROUND_LOOKUP.get(campgroundId, f"Campground {campgroundId}")
        fully_available_sites = []
        partially_available_sites = []

        for site_id, site_data in all_avail_data.items():
            site_name = site_data.get("site", f"Site {site_id}")
            site_status = {}
            for date in target_dates:
                all_avail = site_data.get("availabilities", {})
                status = all_avail.get(date, "Missing")
                site_status[date] = status
            available_nights = sum(1 for date in target_dates if site_status[date].startswith("Available") or site_status[date].startswith("Open"))
            if available_nights == len(target_dates):
                fully_available_sites.append(f"{site_name} ({available_nights} nights)")
            elif available_nights > 0:
                partially_available_sites.append(f"{site_name} ({available_nights} nights)")
            all_sites[site_id] = site_name

        results[campgroundId] = {
            "campground_name": campground_name,
            "target_dates": target_dates,
            "fully_available_sites": fully_available_sites,
            "partially_available_sites": partially_available_sites,
        }

    results["all_sites"] = all_sites

    # Return JSON if requested by JS, else HTML for browser
    import inspect
    frame = inspect.currentframe()
    request = None
    while frame:
        if "request" in frame.f_locals:
            request = frame.f_locals["request"]
            break
        frame = frame.f_back

    if request and "application/json" in request.headers.get("accept", ""):
        return JSONResponse(content=results)
    else:
        html = "<html><head><title>Availability Results</title></head><body>"
        html += f"<h1>Availability from {startDate} to {endDate}</h1>"
        for cid, info in results.items():
            if cid == "all_sites":
                continue
            # Always use the name from CAMPGROUND_LOOKUP for the link text
            campground_url = f"https://www.recreation.gov/camping/campgrounds/{cid}"
            campground_name = CAMPGROUND_LOOKUP.get(str(cid), info.get("campground_name", str(cid)))
            if "error" in info:
                html += (
                    f"<h2><a href='{campground_url}' target='_blank'>{campground_name}</a></h2>"
                    f"<p style='color:red;'>{info['error']}</p>"
                )
                continue
            html += f"<h2><a href='{campground_url}' target='_blank'>{campground_name}</a></h2>"
            html += "<b>Fully Available Sites:</b>"
            if info['fully_available_sites']:
                html += "<ul>"
                for site in info['fully_available_sites']:
                    html += f"<li>{site}</li>"
                html += "</ul>"
            else:
                html += " None<br>"
            html += "<b>Partially Available Sites:</b>"
            if info['partially_available_sites']:
                html += "<ul>"
                for site in info['partially_available_sites']:
                    html += f"<li>{site}</li>"
                html += "</ul>"
            else:
                html += " None<br>"
        html += "</body></html>"
        return HTMLResponse(content=html)
