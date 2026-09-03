function updateUrlWithCoords(lat, lng) {
    const url = new URL(window.parent.location.href);
    url.searchParams.set('map_lat', lat);
    url.searchParams.set('map_lon', lng);
    window.parent.location.href = url.toString();
}
