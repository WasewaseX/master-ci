public class Svc
{
    public string Html(string[] parts)
    {
        var html = "";
        foreach (var p in parts) { html += "<li>"; }
        return html;
    }
}
