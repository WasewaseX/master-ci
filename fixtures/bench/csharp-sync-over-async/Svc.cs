public class Svc
{
    public string Load()
    {
        var task = FetchAsync();
        return task.Result;
    }
}
