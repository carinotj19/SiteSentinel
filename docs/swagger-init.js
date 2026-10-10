"use strict";

// Swagger UI uses the checked-in, same-origin OpenAPI specification.
// Tokens are kept only in the page session, not persisted by Swagger UI.
window.addEventListener("load", () => {
    SwaggerUIBundle({
        url: "/api/openapi.json",
        dom_id: "#swagger-ui",
        deepLinking: true,
        displayRequestDuration: true,
        persistAuthorization: false,
    });
});
