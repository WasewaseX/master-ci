void* Load(const char* path);

void* OpenDefaults(void)
{
    return Load(nullptr);
}
