char* Alias(const char* text)
{
    return const_cast<char*>(text);
}

void* Raw(void* page)
{
    return reinterpret_cast<void*>(page);
}

int Round(double total)
{
    return static_cast<int>(total);
}
