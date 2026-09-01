package com.agomonia.docintel;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;

public final class DocIntelClient {
  private final String baseUrl;
  private final String accessToken;
  private final String workspaceId;
  private final HttpClient http = HttpClient.newHttpClient();

  public DocIntelClient(String baseUrl, String accessToken, String workspaceId) {
    this.baseUrl = baseUrl.replaceAll("/$", "");
    this.accessToken = accessToken;
    this.workspaceId = workspaceId;
  }

  public String get(String path) throws Exception {
    HttpRequest.Builder builder = HttpRequest.newBuilder(URI.create(baseUrl + path))
        .header("Authorization", "Bearer " + accessToken).header("Accept", "application/json").GET();
    if (workspaceId != null && !workspaceId.isBlank()) builder.header("X-DocIntel-Workspace-ID", workspaceId);
    HttpResponse<String> response = http.send(builder.build(), HttpResponse.BodyHandlers.ofString());
    if (response.statusCode() >= 400) throw new IllegalStateException("DocIntel HTTP " + response.statusCode() + ": " + response.body());
    return response.body();
  }

  public String me() throws Exception { return get("/api/v1/me"); }
  public String documents() throws Exception { return get("/api/v1/documents"); }
  public String workspaces() throws Exception { return get("/api/v1/workspaces"); }
}
